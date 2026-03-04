"""Tests for project CRUD endpoints."""

import pytest

from models import Project


@pytest.mark.anyio
async def test_list_projects_empty(client):
    """List projects should return an empty list when no projects exist."""
    resp = await client.get("/api/projects")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_list_projects_with_data(client, db_engine):
    """List projects should return projects with stats."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(
        name="proj-alpha",
        display_name="Alpha",
        path="/tmp/alpha",
    ))
    db.commit()
    db.close()

    resp = await client.get("/api/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "proj-alpha"
    assert data[0]["display_name"] == "Alpha"
    assert "task_total" in data[0]
    assert "agent_total" in data[0]


@pytest.mark.anyio
async def test_list_projects_excludes_archived(client, db_engine):
    """Archived projects should not appear in the list."""
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(
        name="active-proj",
        display_name="Active",
        path="/tmp/active",
        archived=False,
    ))
    db.add(Project(
        name="archived-proj",
        display_name="Archived",
        path="/tmp/archived",
        archived=True,
    ))
    db.commit()
    db.close()

    resp = await client.get("/api/projects")
    assert resp.status_code == 200
    names = [p["name"] for p in resp.json()]
    assert "active-proj" in names
    assert "archived-proj" not in names


# ---- Model-level project tests ----

def test_project_model_defaults(db_session):
    """Project model should have sensible defaults."""
    proj = Project(
        name="defaults-test",
        display_name="Defaults",
        path="/tmp/defaults",
    )
    db_session.add(proj)
    db_session.commit()
    db_session.refresh(proj)

    assert proj.max_concurrent == 2
    assert proj.default_model == "claude-opus-4-6"
    assert proj.archived is False
    assert proj.git_remote is None
    assert proj.description is None


def test_project_model_primary_key(db_session):
    """Inserting two projects with the same name should fail."""
    db_session.add(Project(name="dup", display_name="A", path="/a"))
    db_session.commit()
    db_session.add(Project(name="dup", display_name="B", path="/b"))
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_project_schema_validation():
    """ProjectCreate schema should reject invalid names."""
    from pydantic import ValidationError
    from schemas import ProjectCreate

    # Valid
    ProjectCreate(name="my-project")
    ProjectCreate(name="proj.v2")
    ProjectCreate(name="A123_test")

    # Invalid: starts with dash
    with pytest.raises(ValidationError):
        ProjectCreate(name="-bad")

    # Invalid: contains space
    with pytest.raises(ValidationError):
        ProjectCreate(name="has space")

    # Invalid: empty
    with pytest.raises(ValidationError):
        ProjectCreate(name="")
