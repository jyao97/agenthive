"""Tests for Git and file-related endpoints."""

import pytest

from models import Agent, AgentStatus, Project


# ---- Git endpoints: project not found (404) ----

@pytest.mark.anyio
async def test_git_log_project_not_found(client):
    """GET /api/git/nonexistent/log should return 404 when project doesn't exist."""
    resp = await client.get("/api/git/nonexistent/log")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_git_status_project_not_found(client):
    """GET /api/git/nonexistent/status should return 404 when project doesn't exist."""
    resp = await client.get("/api/git/nonexistent/status")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_git_branches_project_not_found(client):
    """GET /api/git/nonexistent/branches should return 404 when project doesn't exist."""
    resp = await client.get("/api/git/nonexistent/branches")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_git_worktrees_project_not_found(client):
    """GET /api/git/nonexistent/worktrees should return 404 when project doesn't exist."""
    resp = await client.get("/api/git/nonexistent/worktrees")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


# ---- Git endpoints: project exists but no git_manager (503) ----

@pytest.mark.anyio
async def test_git_log_no_git_manager(client, db_engine):
    """GET /api/git/{project}/log should return 503 when git_manager is unavailable."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="git-proj-log", display_name="Git Log", path="/tmp/git-log"))
    db.commit()
    db.close()

    resp = await client.get("/api/git/git-proj-log/log")
    assert resp.status_code == 503
    assert "git manager" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_git_status_no_git_manager(client, db_engine):
    """GET /api/git/{project}/status should return 503 when git_manager is unavailable."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="git-proj-st", display_name="Git Status", path="/tmp/git-st"))
    db.commit()
    db.close()

    resp = await client.get("/api/git/git-proj-st/status")
    assert resp.status_code == 503
    assert "git manager" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_git_branches_no_git_manager(client, db_engine):
    """GET /api/git/{project}/branches should return 503 when git_manager is unavailable."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="git-proj-br", display_name="Git Branches", path="/tmp/git-br"))
    db.commit()
    db.close()

    resp = await client.get("/api/git/git-proj-br/branches")
    assert resp.status_code == 503
    assert "git manager" in resp.json()["detail"].lower()


# ---- Worktree name generation ----

@pytest.mark.anyio
async def test_worktree_name_endpoint(client):
    """POST /api/worktree-name should return a generated name from the prompt."""
    resp = await client.post("/api/worktree-name", json={"prompt": "fix the login page CSS"})
    assert resp.status_code == 200
    data = resp.json()
    assert "name" in data
    # Name should be a non-empty kebab-case string
    name = data["name"]
    assert isinstance(name, str)
    assert len(name) > 0
    assert " " not in name


# ---- Project worktrees ----

@pytest.mark.anyio
async def test_project_worktrees_empty(client, db_engine):
    """GET /api/projects/{name}/worktrees should return empty list when no agents have worktrees."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="wt-empty", display_name="WT Empty", path="/tmp/wt-empty"))
    db.commit()
    db.close()

    resp = await client.get("/api/projects/wt-empty/worktrees")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_project_worktrees_with_agents(client, db_engine):
    """GET /api/projects/{name}/worktrees should return distinct worktree names from agents."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(name="wt-proj", display_name="WT Proj", path="/tmp/wt-proj"))
    db.add(Agent(
        id="wtagent11111",
        project="wt-proj",
        name="Agent WT1",
        status=AgentStatus.IDLE,
        worktree="fix-login",
    ))
    db.add(Agent(
        id="wtagent22222",
        project="wt-proj",
        name="Agent WT2",
        status=AgentStatus.IDLE,
        worktree="add-tests",
    ))
    # Duplicate worktree name — should appear only once in results
    db.add(Agent(
        id="wtagent33333",
        project="wt-proj",
        name="Agent WT3",
        status=AgentStatus.IDLE,
        worktree="fix-login",
    ))
    # Agent with no worktree — should not appear
    db.add(Agent(
        id="wtagent44444",
        project="wt-proj",
        name="Agent WT4",
        status=AgentStatus.IDLE,
        worktree=None,
    ))
    db.commit()
    db.close()

    resp = await client.get("/api/projects/wt-proj/worktrees")
    assert resp.status_code == 200
    data = resp.json()
    assert sorted(data) == ["add-tests", "fix-login"]
