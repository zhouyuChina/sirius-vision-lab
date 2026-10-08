"""Password hashing and signed, expiring admin session tokens."""
import base64
import hashlib
import hmac
import json
import secrets
import time


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=64)
    return 'scrypt$' + salt.hex() + '$' + digest.hex()


def verify_password(password: str, encoded: str) -> bool:
    try:
        kind, salt, expected = encoded.split('$')
        if kind != 'scrypt':
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=64)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def issue_session(secret: str, ttl: int) -> tuple[str, dict]:
    data = {'exp': int(time.time()) + ttl, 'csrf': secrets.token_urlsafe(32)}
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + '.' + signature, data


def read_session(token: str, secret: str) -> dict | None:
    if len(secret) < 32 or len(token) > 2048:
        return None
    try:
        payload, signature = token.split('.')
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload))
        if data['exp'] <= time.time() or not isinstance(data['csrf'], str):
            return None
        return data
    except (ValueError, KeyError, TypeError):
        return None
