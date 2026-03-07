"""Comprehensive tests for Projects API endpoints, models, and schemas."""

import pytest
from pydantic import ValidationError

from models import Agent, AgentMode, AgentStatus, Project, Task, TaskStatus
from schemas import ProjectCreate, ProjectOut, ProjectRename, ProjectWithStats


# ---------------------------------------------------------------------------
# Project listing tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_list_projects_with_stats(client, db_engine):
    """Create project with agents/tasks, verify task_total and agent_total counts."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()

    db.add(Project(
        name="stats-proj",
        display_name="Stats Project",
        path="/tmp/stats-proj",
    ))
    db.commit()

    # Add two agents
    db.add(Agent(
        id="agent0000001",
        project="stats-proj",
        name="Agent One",
        mode=AgentMode.AUTO,
        status=AgentStatus.IDLE,
    ))
    db.add(Agent(
        id="agent0000002",
        project="stats-proj",
        name="Agent Two",
        mode=AgentMode.AUTO,
        status=AgentStatus.STOPPED,
    ))
    db.commit()

    # Add three tasks (using legacy `project` column which the list endpoint queries)
    db.add(Task(title="Task A", project="stats-proj", status=TaskStatus.COMPLETE))
    db.add(Task(title="Task B", project="stats-proj", status=TaskStatus.EXECUTING))
    db.add(Task(title="Task C", project="stats-proj", status=TaskStatus.FAILED))
    db.commit()
    db.close()

    resp = await client.get("/api/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1

    proj = data[0]
    assert proj["name"] == "stats-proj"
    assert proj["task_total"] == 3
    assert proj["agent_total"] == 2


@pytest.mark.anyio
async def test_list_projects_sorted_by_name(client, db_engine):
    """Multiple projects should be returned in alphabetical order by name."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()

    for name in ["zebra-proj", "alpha-proj", "middle-proj"]:
        db.add(Project(
            name=name,
            display_name=name.title(),
            path=f"/tmp/{name}",
        ))
    db.commit()
    db.close()

    resp = await client.get("/api/projects")
    assert resp.status_code == 200
    names = [p["name"] for p in resp.json()]
    assert names == ["alpha-proj", "middle-proj", "zebra-proj"]


@pytest.mark.anyio
async def test_list_projects_with_archived_filter(client, db_engine):
    """Archived projects should be excluded from the default project list."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()

    db.add(Project(
        name="visible-proj",
        display_name="Visible",
        path="/tmp/visible",
        archived=False,
    ))
    db.add(Project(
        name="hidden-proj",
        display_name="Hidden",
        path="/tmp/hidden",
        archived=True,
    ))
    db.commit()
    db.close()

    resp = await client.get("/api/projects")
    assert resp.status_code == 200
    names = [p["name"] for p in resp.json()]
    assert "visible-proj" in names
    assert "hidden-proj" not in names


# ---------------------------------------------------------------------------
# Project settings tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_update_project_settings_auto_progress(client, db_engine):
    """PATCH /api/projects/{name}/settings with auto_progress_summary=True."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(
        name="settings-proj",
        display_name="Settings Proj",
        path="/tmp/settings-proj",
    ))
    db.commit()
    db.close()

    resp = await client.patch(
        "/api/projects/settings-proj/settings",
        json={"auto_progress_summary": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["auto_progress_summary"] is True

    # Verify it persisted in the DB
    db2 = Session()
    persisted = db2.get(Project, "settings-proj")
    assert persisted.auto_progress_summary is True
    db2.close()


@pytest.mark.anyio
async def test_update_project_settings_not_found(client):
    """PATCH settings for a non-existent project should return 404."""
    resp = await client.patch(
        "/api/projects/nonexistent-proj/settings",
        json={"auto_progress_summary": True},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Project rename tests
# ---------------------------------------------------------------------------

def test_project_rename_schema_validation():
    """ProjectRename should accept valid names and reject invalid ones."""
    # Valid names
    pr = ProjectRename(new_name="new-project")
    assert pr.new_name == "new-project"

    pr2 = ProjectRename(new_name="proj.v2", display_name="Project V2")
    assert pr2.display_name == "Project V2"

    pr3 = ProjectRename(new_name="A123_test")
    assert pr3.new_name == "A123_test"

    # Invalid: starts with dash
    with pytest.raises(ValidationError):
        ProjectRename(new_name="-bad-name")

    # Invalid: contains space
    with pytest.raises(ValidationError):
        ProjectRename(new_name="has space")

    # Invalid: empty
    with pytest.raises(ValidationError):
        ProjectRename(new_name="")

    # Invalid: special characters
    with pytest.raises(ValidationError):
        ProjectRename(new_name="proj@name")


@pytest.mark.anyio
async def test_project_rename_endpoint_not_found(client):
    """PUT /api/projects/{name}/rename for a non-existent project should return 404."""
    resp = await client.put(
        "/api/projects/ghost-project/rename",
        json={"new_name": "new-ghost"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Project archive/delete tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_archive_project_not_found(client):
    """POST /api/projects/{name}/archive should return 404 for non-existent project."""
    resp = await client.post("/api/projects/nonexistent-proj/archive")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_delete_project_not_found(client):
    """DELETE /api/projects/{name} should return 404 when neither DB nor disk has the project."""
    resp = await client.delete("/api/projects/nonexistent-proj")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

def test_project_model_with_all_fields(db_session):
    """Create a project with all optional fields set and verify persistence."""
    proj = Project(
        name="full-proj",
        display_name="Full Project",
        path="/tmp/full-proj",
        git_remote="https://github.com/example/full-proj.git",
        description="A project with every field populated.",
        max_concurrent=5,
        default_model="claude-opus-4-6",
        archived=True,
        auto_progress_summary=True,
    )
    db_session.add(proj)
    db_session.commit()
    db_session.refresh(proj)

    assert proj.name == "full-proj"
    assert proj.display_name == "Full Project"
    assert proj.path == "/tmp/full-proj"
    assert proj.git_remote == "https://github.com/example/full-proj.git"
    assert proj.description == "A project with every field populated."
    assert proj.max_concurrent == 5
    assert proj.default_model == "claude-opus-4-6"
    assert proj.archived is True
    assert proj.auto_progress_summary is True


def test_project_model_description_and_git_remote(db_session):
    """Verify description and git_remote persist correctly."""
    proj = Project(
        name="desc-proj",
        display_name="Desc",
        path="/tmp/desc-proj",
        description="Some detailed description\nwith newlines.",
        git_remote="git@github.com:org/repo.git",
    )
    db_session.add(proj)
    db_session.commit()
    db_session.refresh(proj)

    assert proj.description == "Some detailed description\nwith newlines."
    assert proj.git_remote == "git@github.com:org/repo.git"


def test_project_max_concurrent_default(db_session):
    """Default max_concurrent should be 2."""
    proj = Project(
        name="default-mc",
        display_name="Defaults MC",
        path="/tmp/default-mc",
    )
    db_session.add(proj)
    db_session.commit()
    db_session.refresh(proj)

    assert proj.max_concurrent == 2


def test_project_default_model_value(db_session):
    """Default model should be claude-opus-4-6."""
    proj = Project(
        name="default-model",
        display_name="Defaults Model",
        path="/tmp/default-model",
    )
    db_session.add(proj)
    db_session.commit()
    db_session.refresh(proj)

    assert proj.default_model == "claude-opus-4-6"


def test_project_create_schema_valid_names():
    """ProjectCreate should accept various valid name patterns."""
    valid_names = [
        "my-project",
        "proj.v2",
        "A123_test",
        "project",
        "x",
        "MyProject",
        "proj-1.0_beta",
        "1start-with-number",
    ]
    for name in valid_names:
        pc = ProjectCreate(name=name)
        assert pc.name == name


def test_project_create_schema_rejects_invalid():
    """ProjectCreate should reject names with spaces, special chars, empty, or starting with dash."""
    invalid_names = [
        "",           # empty
        "-starts-bad",  # starts with dash
        ".starts-dot",  # starts with dot
        "has space",  # spaces
        "special@char",  # special characters
        "slash/name",  # slash
        "back\\slash",  # backslash
    ]
    for name in invalid_names:
        with pytest.raises(ValidationError):
            ProjectCreate(name=name)


# ---------------------------------------------------------------------------
# Project tree/browse tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_project_tree_not_found(client):
    """GET /api/projects/nonexistent/tree should return 404."""
    resp = await client.get("/api/projects/nonexistent/tree")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_project_browse_not_found(client):
    """GET /api/projects/nonexistent/browse should return 404."""
    resp = await client.get("/api/projects/nonexistent/browse", params={"path": "README.md"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# ProjectWithStats schema tests
# ---------------------------------------------------------------------------

def test_project_with_stats_defaults():
    """All stat fields in ProjectWithStats should default to 0 / None."""
    stats = ProjectWithStats(
        name="stat-test",
        display_name="Stat Test",
        path="/tmp/stat-test",
    )
    assert stats.task_total == 0
    assert stats.task_completed == 0
    assert stats.task_failed == 0
    assert stats.task_running == 0
    assert stats.agent_total == 0
    assert stats.agent_active == 0
    assert stats.last_activity is None


# ---------------------------------------------------------------------------
# Trash endpoint tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_trash_list_empty(client):
    """GET /api/projects/trash should return an empty list when no trash exists."""
    resp = await client.get("/api/projects/trash")
    assert resp.status_code == 200
    # The response is a list (possibly empty since .trash dir may not exist)
    assert isinstance(resp.json(), list)


@pytest.mark.anyio
async def test_trash_restore_not_found(client):
    """POST /api/projects/trash/nonexistent/restore should return 404."""
    resp = await client.post("/api/projects/trash/nonexistent/restore")
    assert resp.status_code == 404
