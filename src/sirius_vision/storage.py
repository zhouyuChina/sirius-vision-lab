"""SQLite WAL persistence and content-addressed image storage."""
import hashlib
import json
import logging
import math
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)
JSON_FIELDS = ('input_options', 'parsed_fields')


class Storage:
    def __init__(self, root: Path):
        self.root = root
        (root / 'data').mkdir(parents=True, exist_ok=True)
        self.path = root / 'data/vision.sqlite3'
        with self.connect() as conn:
            conn.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS records (
                    id TEXT PRIMARY KEY, task TEXT NOT NULL, template_version TEXT NOT NULL,
                    provider TEXT, model TEXT, status TEXT NOT NULL, input_sha256 TEXT NOT NULL,
                    input_image TEXT NOT NULL, input_options TEXT NOT NULL, raw_output TEXT,
                    parsed_fields TEXT, error TEXT, latency_ms INTEGER NOT NULL, tokens INTEGER NOT NULL,
                    created_at TEXT NOT NULL, review TEXT CHECK(review IN ('correct','wrong','uncertain')),
                    review_note TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS records_created ON records(created_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS records_task_status ON records(task, status, created_at);
                CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, expires_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS api_keys (
                    key_hash TEXT PRIMARY KEY, label TEXT NOT NULL, role TEXT NOT NULL
                    CHECK(role IN ('client','admin')), created_at TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
                );
            ''')

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def save_image(self, data: bytes, ext: str) -> tuple[str, str]:
        digest = hashlib.sha256(data).hexdigest()
        relative = f'data/{digest[:2]}/{digest}.{ext}'
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation avoids replacing an already persisted identical image.
        try:
            with path.open('xb') as output:
                output.write(data)
        except FileExistsError:
            pass
        return digest, relative

    def insert(self, record: dict[str, Any]) -> None:
        values = {k: json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS else v for k, v in record.items()}
        with self.connect() as conn:
            conn.execute(f'INSERT INTO records ({",".join(values)}) VALUES ({",".join("?" for _ in values)})', tuple(values.values()))
        logger.info('record_saved record_id=%s status=%s', record['id'], record['status'])

    @staticmethod
    def decode(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for key in JSON_FIELDS:
            result[key] = json.loads(result[key]) if result[key] else None
        return result

    def get(self, record_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM records WHERE id=?', (record_id,)).fetchone()
        return self.decode(row) if row else None

    def list_records(self, task: str | None = None, status: str | None = None,
                     date_from: str | None = None, date_to: str | None = None,
                     cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
        conditions, args = [], []
        for field, value, operator in [('task', task, '='), ('status', status, '='),
                                        ('created_at', date_from, '>='), ('created_at', date_to, '<=')]:
            if value:
                conditions.append(f'{field} {operator} ?')
                args.append(value)
        if cursor:
            previous = self.get(cursor)
            if not previous:
                raise ValueError('Invalid cursor')
            conditions.append('(created_at, id) < (?, ?)')
            args.extend([previous['created_at'], previous['id']])
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        with self.connect() as conn:
            rows = conn.execute('SELECT * FROM records' + where + ' ORDER BY created_at DESC, id DESC LIMIT ?',
                                (*args, limit + 1)).fetchall()
        items = [self.decode(row) for row in rows[:limit]]
        return {'items': items, 'next_cursor': items[-1]['id'] if len(rows) > limit else None}

    def review(self, record_id: str, review: str, note: str) -> bool:
        with self.connect() as conn:
            changed = conn.execute('UPDATE records SET review=?, review_note=? WHERE id=?', (review, note, record_id)).rowcount
        logger.info('record_review record_id=%s updated=%s', record_id, bool(changed))
        return bool(changed)

    def stats(self) -> dict[str, Any]:
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        with self.connect() as conn:
            rows = conn.execute('SELECT task, latency_ms, tokens, review FROM records WHERE created_at>=?', (since,)).fetchall()
        latencies = sorted(row['latency_ms'] for row in rows)
        counts: dict[str, int] = {}
        for row in rows:
            counts[row['task']] = counts.get(row['task'], 0) + 1
        def percentile(p: float) -> int:
            return latencies[max(0, math.ceil(len(latencies) * p) - 1)] if latencies else 0
        return {'period_days': 7, 'calls': len(rows), 'latency_p50_ms': percentile(.5),
                'latency_p95_ms': percentile(.95), 'tokens': sum(r['tokens'] for r in rows),
                'per_task': counts, 'reviewed': sum(r['review'] is not None for r in rows)}

    def issue_key(self, label: str, role: str = 'client') -> str:
        key = secrets.token_urlsafe(32)
        with self.connect() as conn:
            conn.execute('INSERT INTO api_keys(key_hash,label,role,created_at) VALUES(?,?,?,?)',
                         (hashlib.sha256(key.encode()).hexdigest(), label, role, datetime.now(timezone.utc).isoformat()))
        return key

    def key_role(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute('SELECT role FROM api_keys WHERE key_hash=? AND revoked=0',
                               (hashlib.sha256(key.encode()).hexdigest(),)).fetchone()
        return row['role'] if row else None

    def add_session(self, token: str, expiry: int) -> None:
        with self.connect() as conn:
            conn.execute('DELETE FROM sessions WHERE expires_at<=?', (time.time(),))
            conn.execute('INSERT INTO sessions VALUES(?,?)', (hashlib.sha256(token.encode()).hexdigest(), expiry))

    def session_active(self, token: str) -> bool:
        with self.connect() as conn:
            return conn.execute('SELECT 1 FROM sessions WHERE token_hash=? AND expires_at>?',
                                (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone() is not None

    def remove_session(self, token: str) -> None:
        with self.connect() as conn:
            conn.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
