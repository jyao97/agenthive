"""Shared pytest fixtures for AgentHive backend tests."""

import os
import sys

# Ensure orchestrator package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Disable auth for all tests
os.environ["DISABLE_AUTH"] = "1"
# Use in-memory DB
os.environ["DB_PATH"] = ":memory:"

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from models import Agent, AgentMode, AgentStatus, Base, Message, MessageRole, MessageStatus, Project, Task, TaskStatus


@pytest.fixture()
def db_engine():
    """Create an in-memory SQLite engine with all tables."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    """Provide a transactional DB session scoped to each test."""
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    session = Session()
    yield session
    session.close()


@pytest.fixture()
def client(db_engine):
    """Create a test HTTP client with the DB dependency overridden."""
    from httpx import ASGITransport, AsyncClient
    from database import get_db
    from main import app

    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)

    def _override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def sample_project(db_session):
    """Insert and return a sample project row."""
    proj = Project(
        name="test-project",
        display_name="Test Project",
        path="/tmp/test-project",
        max_concurrent=2,
        default_model="claude-opus-4-6",
    )
    db_session.add(proj)
    db_session.commit()
    db_session.refresh(proj)
    return proj


@pytest.fixture()
def sample_agent(db_session, sample_project):
    """Insert and return a sample agent row."""
    agent = Agent(
        id="aaaa11112222",
        project=sample_project.name,
        name="Test agent prompt...",
        mode=AgentMode.AUTO,
        status=AgentStatus.IDLE,
        model="claude-opus-4-6",
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    return agent


@pytest.fixture()
def sample_task(db_session, sample_project):
    """Insert and return a sample v2 task in INBOX status."""
    task = Task(
        title="Fix the login page",
        description="The login page has a CSS bug",
        project_name=sample_project.name,
        status=TaskStatus.INBOX,
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)
    return task


@pytest.fixture()
def sample_message(db_session, sample_agent):
    """Insert and return a sample message."""
    msg = Message(
        agent_id=sample_agent.id,
        role=MessageRole.USER,
        content="Hello, agent!",
        status=MessageStatus.COMPLETED,
        source="web",
    )
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)
    return msg
