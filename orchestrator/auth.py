"""Authentication utilities — password hashing and token management.

Uses Python stdlib for tokens + bcrypt for password hashing.
Supports transparent migration from legacy SHA-256 hashes on login.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time

import bcrypt

from config import AUTH_TIMEOUT_MINUTES


# ---------------------------------------------------------------------------
# Password hashing (bcrypt with transparent SHA-256 migration)
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Hash a password with bcrypt (cost factor 12)."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against a stored hash.

    Supports two formats:
    - bcrypt: starts with "$2b$"
    - legacy SHA-256: "salt:hex_digest" (auto-migrated on successful verify)
    """
    if stored_hash.startswith("$2b$"):
        return bcrypt.checkpw(password.encode(), stored_hash.encode())

    # Legacy SHA-256 format: "salt:sha256hex"
    if ":" not in stored_hash:
        return False
    salt, h = stored_hash.split(":", 1)
    return hmac.compare_digest(
        hashlib.sha256((salt + password).encode()).hexdigest(), h
    )


def needs_rehash(stored_hash: str) -> bool:
    """Return True if the stored hash uses a legacy algorithm."""
    return not stored_hash.startswith("$2b$")


# ---------------------------------------------------------------------------
# Token management (custom signed payload — stdlib only)
# ---------------------------------------------------------------------------

def create_token(jwt_secret: str, expires_minutes: int | None = None) -> str:
    """Create a signed token with expiry.

    Format: base64(json_payload).base64(signature)

    The server-side expiry is generous (24h by default). The frontend
    handles inactivity-based lock independently by tracking user
    interactions and clearing the token after AUTH_TIMEOUT_MINUTES
    of no activity.
    """
    if expires_minutes is None:
        # 24-hour server-side expiry; frontend enforces the shorter
        # inactivity timeout via its own idle tracker.
        expires_minutes = 24 * 60
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
    except (ValueError, KeyError, json.JSONDecodeError, base64.binascii.Error, UnicodeDecodeError):
        return False


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class LoginRateLimiter:
    """In-memory rate limiter with exponential backoff.

    After `threshold` failures, locks the IP for `base_seconds * 2^(n - threshold)`
    seconds, capped at `max_seconds`. Restarting the server clears all state.
    """

    def __init__(self, threshold: int = 5, base_seconds: int = 60, max_seconds: int = 3600):
        self.threshold = threshold
        self.base_seconds = base_seconds
        self.max_seconds = max_seconds
        # ip -> {"failures": int, "locked_until": float}
        self._state: dict[str, dict] = {}

    def _lockout_duration(self, failures: int) -> int:
        exponent = failures - self.threshold
        return min(self.base_seconds * (2 ** exponent), self.max_seconds)

    def check(self, ip: str) -> tuple[bool, int]:
        """Check if IP is locked. Returns (is_locked, seconds_remaining)."""
        entry = self._state.get(ip)
        if not entry:
            return False, 0
        if entry["failures"] < self.threshold:
            return False, 0
        remaining = entry["locked_until"] - time.time()
        if remaining <= 0:
            return False, 0
        return True, int(remaining) + 1

    def record_failure(self, ip: str) -> tuple[bool, int]:
        """Record a failed attempt. Returns (now_locked, lock_seconds)."""
        entry = self._state.get(ip)
        if not entry:
            entry = {"failures": 0, "locked_until": 0.0}
            self._state[ip] = entry
        entry["failures"] += 1
        if entry["failures"] >= self.threshold:
            duration = self._lockout_duration(entry["failures"])
            entry["locked_until"] = time.time() + duration
            return True, duration
        return False, 0

    def record_success(self, ip: str) -> None:
        """Clear failure count on successful login."""
        self._state.pop(ip, None)


# Singleton — lives in memory, cleared on server restart
login_limiter = LoginRateLimiter()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

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
    """Store a new password hash (bcrypt)."""
    from models import SystemConfig

    h = hash_password(password)
    row = db_session.get(SystemConfig, "password_hash")
    if row:
        row.value = h
    else:
        db_session.add(SystemConfig(key="password_hash", value=h))
    db_session.commit()
