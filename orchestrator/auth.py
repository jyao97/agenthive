"""Authentication utilities — password hashing and token management.

Uses only Python stdlib: hashlib, hmac, secrets, base64, json, time.
No external dependencies needed for single-password auth.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time

from config import AUTH_TIMEOUT_MINUTES


def hash_password(password: str) -> str:
    """Hash a password with a random salt using SHA-256."""
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}:{h}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against a stored salt:hash."""
    if ":" not in stored_hash:
        return False
    salt, h = stored_hash.split(":", 1)
    return hmac.compare_digest(
        hashlib.sha256((salt + password).encode()).hexdigest(), h
    )


def create_token(jwt_secret: str, expires_minutes: int | None = None) -> str:
    """Create a signed token with expiry.

    Format: base64(json_payload).base64(signature)
    """
    if expires_minutes is None:
        expires_minutes = AUTH_TIMEOUT_MINUTES
    payload = {
        "exp": time.time() + expires_minutes * 60,
        "iat": time.time(),
        "jti": secrets.token_hex(8),
    }
    payload_bytes = base64.urlsafe_b64encode(
        json.dumps(payload).encode()
    ).rstrip(b"=")
    sig = hmac.new(
        jwt_secret.encode(), payload_bytes, hashlib.sha256
    ).digest()
    sig_bytes = base64.urlsafe_b64encode(sig).rstrip(b"=")
    return payload_bytes.decode() + "." + sig_bytes.decode()


def verify_token(token: str, jwt_secret: str) -> bool:
    """Verify token signature and check expiry."""
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return False
        payload_b64, sig_b64 = parts

        # Verify signature
        expected_sig = hmac.new(
            jwt_secret.encode(), payload_b64.encode(), hashlib.sha256
        ).digest()
        # Re-pad base64
        sig_padded = sig_b64 + "=" * (-len(sig_b64) % 4)
        actual_sig = base64.urlsafe_b64decode(sig_padded)
        if not hmac.compare_digest(expected_sig, actual_sig):
            return False

        # Check expiry
        payload_padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_padded))
        if payload.get("exp", 0) < time.time():
            return False

        return True
    except Exception:
        return False


def get_jwt_secret(db_session) -> str:
    """Get the JWT secret from SystemConfig, creating if missing."""
    from models import SystemConfig

    row = db_session.get(SystemConfig, "jwt_secret")
    if row:
        return row.value
    secret = secrets.token_hex(32)
    db_session.add(SystemConfig(key="jwt_secret", value=secret))
    db_session.commit()
    return secret


def get_password_hash(db_session) -> str | None:
    """Get stored password hash, or None if not set."""
    from models import SystemConfig

    row = db_session.get(SystemConfig, "password_hash")
    return row.value if row else None


def set_password_hash(db_session, password: str) -> None:
    """Store a new password hash."""
    from models import SystemConfig

    h = hash_password(password)
    row = db_session.get(SystemConfig, "password_hash")
    if row:
        row.value = h
    else:
        db_session.add(SystemConfig(key="password_hash", value=h))
    db_session.commit()
