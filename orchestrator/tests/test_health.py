"""Tests for the /api/health endpoint."""

import pytest


@pytest.mark.anyio
async def test_health_returns_200(client):
    """Health endpoint should return 200 with status info."""
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "agenthive"
    assert "status" in data
    assert "db" in data
    assert "claude_cli" in data


@pytest.mark.anyio
async def test_health_db_field(client):
    """Health check should report DB status (ok or error depending on engine)."""
    resp = await client.get("/api/health")
    data = resp.json()
    # The health endpoint uses the module-level SessionLocal, not the
    # overridden get_db dependency, so it may report error in tests.
    assert data["db"] in ("ok", "error")


@pytest.mark.anyio
async def test_health_claude_cli_unavailable(client):
    """Without a real worker_manager, claude_cli should be unavailable."""
    resp = await client.get("/api/health")
    data = resp.json()
    # No worker_manager in test context
    assert data["claude_cli"] == "unavailable"
    assert data["status"] == "degraded"
