"""Tests for system, notifications, auth-extended, push, logs, and process endpoints."""

import os

import pytest


# ---------------------------------------------------------------------------
# System stats
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_system_stats_returns_cpu(client):
    """GET /api/system/stats should include a 'cpu' key."""
    resp = await client.get("/api/system/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "cpu" in data


@pytest.mark.anyio
async def test_system_stats_returns_memory(client):
    """GET /api/system/stats should include a 'memory' key."""
    resp = await client.get("/api/system/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "memory" in data


@pytest.mark.anyio
async def test_system_stats_returns_disk(client):
    """GET /api/system/stats should include a 'disk' key."""
    resp = await client.get("/api/system/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "disk" in data


# ---------------------------------------------------------------------------
# System storage
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_system_storage_structure(client):
    """GET /api/system/storage should return categories array and total_bytes."""
    resp = await client.get("/api/system/storage")
    assert resp.status_code == 200
    data = resp.json()
    assert "categories" in data
    assert isinstance(data["categories"], list)
    assert "total_bytes" in data
    assert isinstance(data["total_bytes"], int)


# ---------------------------------------------------------------------------
# System orphans
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_system_orphan_scan(client, db_engine, monkeypatch):
    """GET /api/system/orphans/scan should return a result dict."""
    # Patch scan_orphans to avoid needing the real DB / filesystem
    from main import app
    monkeypatch.setattr(
        "orphan_cleanup.scan_orphans",
        lambda: {"orphan_session_count": 0, "orphan_log_count": 0, "total_freed_bytes": 0},
    )

    resp = await client.get("/api/system/orphans/scan")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# Notification settings
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_get_notification_settings_default(client):
    """GET /api/settings/notifications should default to both enabled."""
    resp = await client.get("/api/settings/notifications")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agents_enabled"] is True
    assert data["tasks_enabled"] is True


@pytest.mark.anyio
async def test_update_notification_agents_disabled(client):
    """PUT /api/settings/notifications with agents_enabled=false should persist."""
    resp = await client.put(
        "/api/settings/notifications",
        json={"agents_enabled": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["agents_enabled"] is False
    # tasks_enabled should remain at default True
    assert data["tasks_enabled"] is True


@pytest.mark.anyio
async def test_update_notification_tasks_disabled(client):
    """PUT /api/settings/notifications with tasks_enabled=false should persist."""
    resp = await client.put(
        "/api/settings/notifications",
        json={"tasks_enabled": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["tasks_enabled"] is False
    # agents_enabled should remain at default True
    assert data["agents_enabled"] is True


@pytest.mark.anyio
async def test_notification_settings_roundtrip(client):
    """Disable then re-enable notification settings and verify state."""
    # Disable both
    resp = await client.put(
        "/api/settings/notifications",
        json={"agents_enabled": False, "tasks_enabled": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["agents_enabled"] is False
    assert data["tasks_enabled"] is False

    # Re-enable both
    resp = await client.put(
        "/api/settings/notifications",
        json={"agents_enabled": True, "tasks_enabled": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["agents_enabled"] is True
    assert data["tasks_enabled"] is True

    # Confirm via GET
    resp = await client.get("/api/settings/notifications")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agents_enabled"] is True
    assert data["tasks_enabled"] is True


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_health_service_name(client):
    """Health endpoint should return service='xylocopa'."""
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "xylocopa"


@pytest.mark.anyio
async def test_health_response_schema(client):
    """Health endpoint should contain all expected fields."""
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    for field in ("status", "service", "db", "claude_cli"):
        assert field in data, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# Auth extended
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_auth_check_with_disable_auth(client):
    """With DISABLE_AUTH=1, auth/check should return authenticated=True."""
    assert os.environ.get("DISABLE_AUTH") == "1"
    resp = await client.post("/api/auth/check")
    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is True


@pytest.mark.anyio
async def test_auth_change_password_without_existing(client):
    """POST /api/auth/change-password should fail when no password is set."""
    resp = await client.post(
        "/api/auth/change-password",
        json={"current_password": "old", "new_password": "newpass"},
    )
    # No password stored in fresh in-memory DB -> 400 "No password set"
    assert resp.status_code == 400
    data = resp.json()
    assert "No password set" in data.get("detail", "")


# ---------------------------------------------------------------------------
# Push notifications
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_vapid_public_key(client, monkeypatch):
    """GET /api/push/vapid-public-key should return a key when configured."""
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "test-vapid-key-123")
    # Patch the config module attribute so the endpoint sees the value
    import config
    monkeypatch.setattr(config, "VAPID_PUBLIC_KEY", "test-vapid-key-123")

    resp = await client.get("/api/push/vapid-public-key")
    assert resp.status_code == 200
    data = resp.json()
    assert "publicKey" in data
    assert data["publicKey"] == "test-vapid-key-123"


@pytest.mark.anyio
async def test_push_unsubscribe_no_endpoint(client):
    """POST /api/push/unsubscribe with missing endpoint should return 400."""
    resp = await client.post("/api/push/unsubscribe", json={})
    assert resp.status_code == 400
    data = resp.json()
    assert "Missing endpoint" in data.get("detail", "")


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_logs_endpoint(client):
    """GET /api/logs should return a dict with a 'lines' key."""
    resp = await client.get("/api/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert "lines" in data
    assert isinstance(data["lines"], list)


# ---------------------------------------------------------------------------
# Processes / Workers
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_processes_endpoint(client):
    """GET /api/processes should return a list."""
    resp = await client.get("/api/processes")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


@pytest.mark.anyio
async def test_workers_endpoint(client):
    """GET /api/workers should return a list."""
    resp = await client.get("/api/workers")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
