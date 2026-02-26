"""Database session management."""

import os

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from config import DB_PATH

# Ensure directory exists
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    echo=False,
)


# Enable WAL mode for better concurrent read performance
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    """FastAPI dependency for DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables if they don't exist, and run lightweight migrations."""
    from models import Base
    Base.metadata.create_all(bind=engine)

    # Lightweight migrations for existing databases
    with engine.connect() as conn:
        # Add description column to projects if missing
        result = conn.execute(text("PRAGMA table_info(projects)"))
        columns = {row[1] for row in result}
        if "description" not in columns:
            conn.execute(text("ALTER TABLE projects ADD COLUMN description TEXT"))
            conn.commit()

        # Add session_id column to agents if missing
        result = conn.execute(text("PRAGMA table_info(agents)"))
        columns = {row[1] for row in result}
        if "session_id" not in columns:
            conn.execute(text("ALTER TABLE agents ADD COLUMN session_id VARCHAR(100)"))
            conn.commit()

        # Add worktree column to agents if missing
        result = conn.execute(text("PRAGMA table_info(agents)"))
        columns = {row[1] for row in result}
        if "worktree" not in columns:
            conn.execute(text("ALTER TABLE agents ADD COLUMN worktree VARCHAR(200)"))
            conn.commit()

        # Migrate priority → mode column on agents table
        result = conn.execute(text("PRAGMA table_info(agents)"))
        columns = {row[1] for row in result}
        if "mode" not in columns:
            # Add mode column with default AUTO
            conn.execute(text(
                "ALTER TABLE agents ADD COLUMN mode VARCHAR(9) NOT NULL DEFAULT 'AUTO'"
            ))
            # Migrate existing priority values: P0/P1 → PLAN, P2 → AUTO
            if "priority" in columns:
                conn.execute(text(
                    "UPDATE agents SET mode = CASE "
                    "WHEN priority IN ('P0', 'P1') THEN 'PLAN' "
                    "ELSE 'AUTO' END"
                ))
            conn.commit()

        # Same migration for tasks table
        result = conn.execute(text("PRAGMA table_info(tasks)"))
        columns = {row[1] for row in result}
        if "mode" not in columns:
            conn.execute(text(
                "ALTER TABLE tasks ADD COLUMN mode VARCHAR(9) NOT NULL DEFAULT 'AUTO'"
            ))
            if "priority" in columns:
                conn.execute(text(
                    "UPDATE tasks SET mode = CASE "
                    "WHEN priority IN ('P0', 'P1') THEN 'PLAN' "
                    "ELSE 'AUTO' END"
                ))
            conn.commit()

        # Add archived column to projects if missing
        result = conn.execute(text("PRAGMA table_info(projects)"))
        columns = {row[1] for row in result}
        if "archived" not in columns:
            conn.execute(text(
                "ALTER TABLE projects ADD COLUMN archived BOOLEAN NOT NULL DEFAULT 0"
            ))
            conn.commit()

        # Add cli_sync column to agents if missing
        result = conn.execute(text("PRAGMA table_info(agents)"))
        columns = {row[1] for row in result}
        if "cli_sync" not in columns:
            conn.execute(text(
                "ALTER TABLE agents ADD COLUMN cli_sync BOOLEAN NOT NULL DEFAULT 0"
            ))
            conn.commit()

        # Add model column to agents if missing
        result = conn.execute(text("PRAGMA table_info(agents)"))
        columns = {row[1] for row in result}
        if "model" not in columns:
            conn.execute(text(
                "ALTER TABLE agents ADD COLUMN model VARCHAR(100)"
            ))
            conn.commit()

        # Add default_model column to projects if missing
        result = conn.execute(text("PRAGMA table_info(projects)"))
        columns = {row[1] for row in result}
        if "default_model" not in columns:
            conn.execute(text(
                "ALTER TABLE projects ADD COLUMN default_model VARCHAR(100) NOT NULL DEFAULT 'claude-opus-4-6'"
            ))
            conn.commit()

        # Add tmux_pane column to agents if missing
        result = conn.execute(text("PRAGMA table_info(agents)"))
        columns = {row[1] for row in result}
        if "tmux_pane" not in columns:
            conn.execute(text(
                "ALTER TABLE agents ADD COLUMN tmux_pane VARCHAR(100)"
            ))
            conn.commit()

        # Add scheduled_at column to messages if missing
        result = conn.execute(text("PRAGMA table_info(messages)"))
        columns = {row[1] for row in result}
        if "scheduled_at" not in columns:
            conn.execute(text(
                "ALTER TABLE messages ADD COLUMN scheduled_at DATETIME"
            ))
            conn.commit()

        # Add source column to messages if missing
        result = conn.execute(text("PRAGMA table_info(messages)"))
        columns = {row[1] for row in result}
        if "source" not in columns:
            conn.execute(text(
                "ALTER TABLE messages ADD COLUMN source VARCHAR(20)"
            ))
            conn.commit()

        # Add skip_permissions column to agents if missing
        result = conn.execute(text("PRAGMA table_info(agents)"))
        columns = {row[1] for row in result}
        if "skip_permissions" not in columns:
            conn.execute(text(
                "ALTER TABLE agents ADD COLUMN skip_permissions BOOLEAN NOT NULL DEFAULT 1"
            ))
            conn.commit()

        if "muted" not in columns:
            conn.execute(text(
                "ALTER TABLE agents ADD COLUMN muted BOOLEAN NOT NULL DEFAULT 0"
            ))
            conn.commit()

        # Drop old priority column now that mode has been migrated
        result = conn.execute(text("PRAGMA table_info(agents)"))
        columns = {row[1] for row in result}
        if "priority" in columns and "mode" in columns:
            conn.execute(text("ALTER TABLE agents DROP COLUMN priority"))
            conn.commit()

        result = conn.execute(text("PRAGMA table_info(tasks)"))
        columns = {row[1] for row in result}
        if "priority" in columns and "mode" in columns:
            conn.execute(text("ALTER TABLE tasks DROP COLUMN priority"))
            conn.commit()

        # Drop plan-related columns (plan mode fully removed)
        result = conn.execute(text("PRAGMA table_info(agents)"))
        columns = {row[1] for row in result}
        if "plan_approved" in columns:
            conn.execute(text("ALTER TABLE agents DROP COLUMN plan_approved"))
            conn.commit()
        # Re-read columns after potential drop
        result = conn.execute(text("PRAGMA table_info(agents)"))
        columns = {row[1] for row in result}
        if "plan" in columns:
            conn.execute(text("ALTER TABLE agents DROP COLUMN plan"))
            conn.commit()

    # Ensure jwt_secret exists in SystemConfig
    from auth import get_jwt_secret
    db = SessionLocal()
    try:
        get_jwt_secret(db)
    finally:
        db.close()
