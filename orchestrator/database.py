"""Database session management."""

import os

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from config import CC_MODEL, DB_PATH, VALID_MODELS

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
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    """FastAPI dependency for DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _table_columns(conn, table_name: str) -> set[str]:
    """Return the set of column names for a SQLite table."""
    result = conn.execute(text(f"PRAGMA table_info({table_name})"))
    return {row[1] for row in result}


def init_db():
    """Create all tables if they don't exist, and run lightweight migrations."""
    from models import Base
    Base.metadata.create_all(bind=engine)

    # Lightweight migrations for existing databases
    with engine.connect() as conn:
        # Add description column to projects if missing
        columns = _table_columns(conn, "projects")
        if "description" not in columns:
            conn.execute(text("ALTER TABLE projects ADD COLUMN description TEXT"))
            conn.commit()

        # Add session_id column to agents if missing
        columns = _table_columns(conn, "agents")
        if "session_id" not in columns:
            conn.execute(text("ALTER TABLE agents ADD COLUMN session_id VARCHAR(100)"))
            conn.commit()

        # Add worktree column to agents if missing
        columns = _table_columns(conn, "agents")
        if "worktree" not in columns:
            conn.execute(text("ALTER TABLE agents ADD COLUMN worktree VARCHAR(200)"))
            conn.commit()

        # Add effort column to agents if missing
        columns = _table_columns(conn, "agents")
        if "effort" not in columns:
            conn.execute(text("ALTER TABLE agents ADD COLUMN effort VARCHAR(10)"))
            conn.commit()

        # Migrate priority → mode column on agents table
        columns = _table_columns(conn, "agents")
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
        columns = _table_columns(conn, "tasks")
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
        columns = _table_columns(conn, "projects")
        if "archived" not in columns:
            conn.execute(text(
                "ALTER TABLE projects ADD COLUMN archived BOOLEAN NOT NULL DEFAULT 0"
            ))
            conn.commit()

        # Add cli_sync column to agents if missing
        columns = _table_columns(conn, "agents")
        if "cli_sync" not in columns:
            conn.execute(text(
                "ALTER TABLE agents ADD COLUMN cli_sync BOOLEAN NOT NULL DEFAULT 0"
            ))
            conn.commit()

        # Add model column to agents if missing
        columns = _table_columns(conn, "agents")
        if "model" not in columns:
            conn.execute(text(
                "ALTER TABLE agents ADD COLUMN model VARCHAR(100)"
            ))
            conn.commit()

        # Add default_model column to projects if missing
        columns = _table_columns(conn, "projects")
        if "default_model" not in columns:
            conn.execute(text(
                "ALTER TABLE projects ADD COLUMN default_model VARCHAR(100) NOT NULL DEFAULT 'claude-opus-4-6'"
            ))
            conn.commit()

        # Add tmux_pane column to agents if missing
        columns = _table_columns(conn, "agents")
        if "tmux_pane" not in columns:
            conn.execute(text(
                "ALTER TABLE agents ADD COLUMN tmux_pane VARCHAR(100)"
            ))
            conn.commit()

        # Add scheduled_at column to messages if missing
        columns = _table_columns(conn, "messages")
        if "scheduled_at" not in columns:
            conn.execute(text(
                "ALTER TABLE messages ADD COLUMN scheduled_at DATETIME"
            ))
            conn.commit()

        # Add source column to messages if missing
        columns = _table_columns(conn, "messages")
        if "source" not in columns:
            conn.execute(text(
                "ALTER TABLE messages ADD COLUMN source VARCHAR(20)"
            ))
            conn.commit()

        # Add metadata column to messages if missing
        columns = _table_columns(conn, "messages")
        if "metadata" not in columns:
            conn.execute(text(
                "ALTER TABLE messages ADD COLUMN metadata TEXT"
            ))
            conn.commit()

        # Add skip_permissions column to agents if missing
        columns = _table_columns(conn, "agents")
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

        if "parent_id" not in columns:
            conn.execute(text(
                "ALTER TABLE agents ADD COLUMN parent_id VARCHAR(12)"
            ))
            conn.commit()

        # Drop old priority column now that mode has been migrated
        columns = _table_columns(conn, "agents")
        if "priority" in columns and "mode" in columns:
            conn.execute(text("ALTER TABLE agents DROP COLUMN priority"))
            conn.commit()

        columns = _table_columns(conn, "tasks")
        if "priority" in columns and "mode" in columns:
            conn.execute(text("ALTER TABLE tasks DROP COLUMN priority"))
            conn.commit()

        # Drop plan-related columns (plan mode fully removed)
        columns = _table_columns(conn, "agents")
        if "plan_approved" in columns:
            conn.execute(text("ALTER TABLE agents DROP COLUMN plan_approved"))
            conn.commit()
        # Re-read columns after potential drop
        columns = _table_columns(conn, "agents")
        if "plan" in columns:
            conn.execute(text("ALTER TABLE agents DROP COLUMN plan"))
            conn.commit()

        # Drop plan-related columns from tasks (plan mode fully removed)
        task_cols_pre = _table_columns(conn, "tasks")
        if "plan_approved" in task_cols_pre:
            conn.execute(text("ALTER TABLE tasks DROP COLUMN plan_approved"))
            conn.commit()
        task_cols_pre = _table_columns(conn, "tasks")
        if "plan" in task_cols_pre:
            conn.execute(text("ALTER TABLE tasks DROP COLUMN plan"))
            conn.commit()

        # --- Task v2 migrations ---
        task_cols = _table_columns(conn, "tasks")

        task_new_cols = {
            "title": "ALTER TABLE tasks ADD COLUMN title VARCHAR(300) NOT NULL DEFAULT ''",
            "description": "ALTER TABLE tasks ADD COLUMN description TEXT",
            "project_name": "ALTER TABLE tasks ADD COLUMN project_name VARCHAR(100)",
            "priority": "ALTER TABLE tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 0",
            "agent_id": "ALTER TABLE tasks ADD COLUMN agent_id VARCHAR(12) REFERENCES agents(id)",
            "worktree_name": "ALTER TABLE tasks ADD COLUMN worktree_name VARCHAR(200)",
            "branch_name": "ALTER TABLE tasks ADD COLUMN branch_name VARCHAR(200)",
            "attempt_number": "ALTER TABLE tasks ADD COLUMN attempt_number INTEGER NOT NULL DEFAULT 1",
            "retry_context": "ALTER TABLE tasks ADD COLUMN retry_context TEXT",
            "review_artifacts": "ALTER TABLE tasks ADD COLUMN review_artifacts TEXT",
            "agent_summary": "ALTER TABLE tasks ADD COLUMN agent_summary TEXT",
            "rejection_reason": "ALTER TABLE tasks ADD COLUMN rejection_reason TEXT",
            "model": "ALTER TABLE tasks ADD COLUMN model VARCHAR(100)",
            "effort": "ALTER TABLE tasks ADD COLUMN effort VARCHAR(10)",
        }
        for col, ddl in task_new_cols.items():
            if col not in task_cols:
                conn.execute(text(ddl))
        conn.commit()

        # Backfill title from prompt, project_name from project
        conn.execute(text(
            "UPDATE tasks SET title = COALESCE(SUBSTR(prompt, 1, 300), '') "
            "WHERE title = '' AND prompt IS NOT NULL"
        ))
        conn.execute(text(
            "UPDATE tasks SET project_name = project "
            "WHERE project_name IS NULL AND project IS NOT NULL"
        ))
        # Migrate COMPLETED → COMPLETE
        conn.execute(text(
            "UPDATE tasks SET status = 'COMPLETE' WHERE status = 'COMPLETED'"
        ))
        conn.commit()

        # Add skip_permissions, sync_mode, scheduled_at to tasks if missing
        task_cols3 = _table_columns(conn, "tasks")
        if "skip_permissions" not in task_cols3:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN skip_permissions BOOLEAN NOT NULL DEFAULT 1"))
        if "sync_mode" not in task_cols3:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN sync_mode BOOLEAN NOT NULL DEFAULT 0"))
        if "scheduled_at" not in task_cols3:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN scheduled_at DATETIME"))
        conn.commit()

        # Add task_id column to agents if missing
        agent_cols = _table_columns(conn, "agents")
        if "task_id" not in agent_cols:
            conn.execute(text(
                "ALTER TABLE agents ADD COLUMN task_id VARCHAR(12)"
            ))
            conn.commit()

        if "is_subagent" not in agent_cols:
            conn.execute(text(
                "ALTER TABLE agents ADD COLUMN is_subagent BOOLEAN NOT NULL DEFAULT 0"
            ))
            conn.commit()
        if "claude_agent_id" not in agent_cols:
            conn.execute(text(
                "ALTER TABLE agents ADD COLUMN claude_agent_id VARCHAR(30)"
            ))
            conn.commit()

        # Fix invalid model names in projects and agents
        _valid_list = ", ".join(f"'{m}'" for m in VALID_MODELS)
        result = conn.execute(text(
            f"UPDATE projects SET default_model = :fallback "
            f"WHERE default_model NOT IN ({_valid_list})"
        ), {"fallback": CC_MODEL})
        if result.rowcount:
            conn.commit()
        result = conn.execute(text(
            f"UPDATE agents SET model = :fallback "
            f"WHERE model IS NOT NULL AND model NOT IN ({_valid_list})"
        ), {"fallback": CC_MODEL})
        if result.rowcount:
            conn.commit()

        task_cols = _table_columns(conn, "tasks")
        if "try_base_commit" not in task_cols:
            conn.execute(text(
                "ALTER TABLE tasks ADD COLUMN try_base_commit VARCHAR(50)"
            ))
            conn.commit()

        if "use_worktree" not in task_cols:
            conn.execute(text(
                "ALTER TABLE tasks ADD COLUMN use_worktree BOOLEAN NOT NULL DEFAULT 1"
            ))
            conn.commit()

    # Ensure jwt_secret exists in SystemConfig
    from auth import get_jwt_secret
    db = SessionLocal()
    try:
        get_jwt_secret(db)
    finally:
        db.close()
