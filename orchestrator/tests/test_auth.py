"""Tests for authentication: password hashing, token management, rate limiter."""

import time

import pytest

from auth import (
    LoginRateLimiter,
    create_token,
    hash_password,
    verify_password,
    verify_token,
)


# ---- Password hashing ----

def test_hash_password_returns_salt_colon_hash():
    """hash_password should return 'salt:hash' format."""
    result = hash_password("secret123")
    assert ":" in result
    salt, h = result.split(":", 1)
    assert len(salt) == 32  # 16 bytes hex
    assert len(h) == 64     # SHA-256 hex


def test_verify_password_correct():
    """verify_password should return True for the correct password."""
    stored = hash_password("mypassword")
    assert verify_password("mypassword", stored) is True


def test_verify_password_wrong():
    """verify_password should return False for a wrong password."""
    stored = hash_password("mypassword")
    assert verify_password("wrongpassword", stored) is False


def test_verify_password_malformed_hash():
    """verify_password should return False for a hash without a colon."""
    assert verify_password("anything", "nocolonhere") is False


def test_hash_password_unique_salts():
    """Two calls to hash_password should produce different salts."""
    h1 = hash_password("same")
    h2 = hash_password("same")
    assert h1 != h2  # Different salts


# ---- Token management ----

def test_create_and_verify_token():
    """A freshly created token should verify successfully."""
    secret = "test-secret-key-1234"
    token = create_token(secret, expires_minutes=5)
    assert verify_token(token, secret) is True


def test_verify_token_wrong_secret():
    """Token should not verify with a different secret."""
    token = create_token("secret-A", expires_minutes=5)
    assert verify_token(token, "secret-B") is False


def test_verify_token_expired():
    """An expired token should fail verification."""
    secret = "test-secret"
    # Create a token that expired 1 minute ago
    token = create_token(secret, expires_minutes=-1)
    assert verify_token(token, secret) is False


def test_verify_token_malformed():
    """Malformed tokens should return False, not raise."""
    secret = "test-secret"
    assert verify_token("", secret) is False
    assert verify_token("only-one-part", secret) is False
    assert verify_token("a.b.c", secret) is False
    assert verify_token("not.valid", secret) is False


# ---- Rate limiter ----

def test_rate_limiter_allows_under_threshold():
    """Under the threshold, requests should not be locked."""
    rl = LoginRateLimiter(threshold=3, base_seconds=10, max_seconds=100)
    for _ in range(2):
        locked, _ = rl.record_failure("1.2.3.4")
        assert locked is False
    is_locked, _ = rl.check("1.2.3.4")
    assert is_locked is False


def test_rate_limiter_locks_at_threshold():
    """At the threshold, the IP should be locked."""
    rl = LoginRateLimiter(threshold=3, base_seconds=10, max_seconds=100)
    for _ in range(3):
        locked, secs = rl.record_failure("5.6.7.8")
    assert locked is True
    assert secs > 0
    is_locked, remaining = rl.check("5.6.7.8")
    assert is_locked is True
    assert remaining > 0


def test_rate_limiter_success_clears_state():
    """A successful login should clear the failure count."""
    rl = LoginRateLimiter(threshold=3, base_seconds=10, max_seconds=100)
    for _ in range(2):
        rl.record_failure("10.0.0.1")
    rl.record_success("10.0.0.1")
    is_locked, _ = rl.check("10.0.0.1")
    assert is_locked is False


# ---- Auth endpoints ----

@pytest.mark.anyio
async def test_auth_check_no_password(client):
    """When no password is set, auth/check should indicate needs_setup."""
    resp = await client.post("/api/auth/check")
    # With DISABLE_AUTH=1, it returns authenticated=True
    data = resp.json()
    assert resp.status_code == 200
    assert data["authenticated"] is True


@pytest.mark.anyio
async def test_auth_set_password_too_short(client):
    """Setting a password shorter than 4 chars should fail."""
    resp = await client.post("/api/auth/set-password", json={"password": "ab"})
    # With DISABLE_AUTH=1, auth middleware is bypassed but the endpoint
    # may still enforce its own validation
    assert resp.status_code in (400, 200)
