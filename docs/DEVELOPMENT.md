# M1 development and operations

Python 3.12 is required. Run `uv sync`, then `uv run pytest -q` and `uv run python -c "from sirius_vision.main import app"`. Default tests inject mocked adapters and HTTP transports; they never call real inference endpoints. `uv run pytest -m slow` selects only real-API smoke, requiring configured provider env and `VISION_SMOKE_IMAGE=/absolute/image/path`; without that path it skips.

Copy `.env.example` to `.env`, fill secrets locally, and load it into the process environment using your process manager or `set -a; source .env; set +a`. The application does not implicitly read other dotenv files. Use quoted values when shell sourcing (especially password hashes containing `$`). Never put real keys in Git.

Select `VISION_PROVIDER=minimax|kimi|glm`; the default is MiniMax. Set the matching endpoint/key variables from `.env.example`. `VISION_BASE_URL` and `VISION_MODEL` override the selected provider. When switching provider, change/unset `VISION_MODEL` too. Backup providers are explicitly selectable; there is no automatic cross-provider fallback. Templates may set `preferred_model` to a configured model to select that provider. Transport timeout is 45 seconds per attempt, at most one retry with a 250 ms backoff on 429/timeout. Other upstream errors are not retried. A schema correction may make a second adapter call (four HTTP attempts maximum overall).

Run locally with `uv run uvicorn sirius_vision.main:app --host 127.0.0.1 --port 8902`. API keys work over loopback HTTP; the admin console requires HTTPS because cookies are always Secure. For local admin verification, use a local HTTPS reverse proxy or uvicorn's `--ssl-certfile` and `--ssl-keyfile`. Production deployment is outside M1. Behind a prefix-stripping proxy, set uvicorn `--root-path /vision` and trust forwarded headers only from that proxy; preserve the public Host and scheme for Origin checks.

Generate a password hash and session secret in a local terminal (outputs are secrets; store them locally):

```sh
uv run python -c 'import getpass; from sirius_vision.auth import hash_password; print(hash_password(getpass.getpass()))'
uv run python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

`VISION_API_KEYS` holds comma-separated ordinary keys. `VISION_ADMIN_API_KEYS` holds comma-separated elevated keys for record access. Both work on other `/v1` routes. Do not use the admin password as an API key. Empty configuration fails closed. The session secret must be at least 32 characters.

Persisted API keys are stored only as SHA-256 digests with label/role/revocation. To issue one locally:

```sh
uv run python -c 'from sirius_vision.config import Settings; from sirius_vision.storage import Storage; print(Storage(Settings().root).issue_key("integration", "client"))'
```

Use role `admin` only for trusted operators. Revoke a persisted key by setting its `api_keys.revoked` column to 1 using a local SQLite administration tool. Environment keys are revoked by removing them from configuration and restarting.

SQLite is initialized at `VISION_ROOT/data/vision.sqlite3`, with WAL, indexed cursor ordering and a 10-second lock timeout. Images live at `VISION_ROOT/data/<first-two-sha256-characters>/<sha256>.<ext>`. Back up the SQLite database through the SQLite backup API plus the image tree; do not copy only the live database file without WAL coordination. M1 creates additive tables/indexes (records, api_keys, sessions), with no existing-data rewrite/backfill. Rolling back the application leaves data untouched; no destructive down migration is supplied. The data directory contains private images and outputs and must not be served as static content.

Dependencies: FastAPI/uvicorn provide ASGI validation and hosting; httpx provides async HTTP plus injectable test transports; jsonschema validates the published schemas directly. Standard-library SQLite and scrypt avoid extra storage/password dependencies. Pytest and pytest-asyncio are development-only. No frontend build/dependencies are required.

Errors intentionally omit upstream payloads. Logs use event names with record IDs for persistence/review and retry reasons without keys, prompts or image URLs. Live inference quality, live endpoint compatibility, deployment, and representative-image evaluation remain M2/M3 acceptance work.
