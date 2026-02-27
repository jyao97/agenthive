#!/usr/bin/env python3
"""One-off script to clean up orphaned session JSONL files and output logs
that no longer belong to any agent in the database.

Run from the cc-orchestrator directory:
    python3 cleanup_orphans.py          # dry run (shows what would be deleted)
    python3 cleanup_orphans.py --delete  # actually delete
"""

import os
import sys
import glob

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "orchestrator"))

from config import CLAUDE_HOME, DB_PATH
from sqlalchemy import create_engine, text

DRY_RUN = "--delete" not in sys.argv


def main():
    db_path = os.path.abspath(DB_PATH)
    if not os.path.isfile(db_path):
        print(f"Database not found: {db_path}")
        sys.exit(1)

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        # All session_ids still in the DB
        rows = conn.execute(text("SELECT session_id FROM agents WHERE session_id IS NOT NULL")).fetchall()
        live_sessions = {r[0] for r in rows}

        # All message IDs still in the DB
        rows = conn.execute(text("SELECT id FROM messages")).fetchall()
        live_msg_ids = {r[0] for r in rows}

    print(f"Live agents with sessions: {len(live_sessions)}")
    print(f"Live messages: {len(live_msg_ids)}")
    print(f"Mode: {'DRY RUN' if DRY_RUN else 'DELETE'}")
    print()

    # 1. Scan session JSONL files
    projects_dir = os.path.join(CLAUDE_HOME, "projects")
    orphan_jsonls = []
    total_jsonls = 0
    if os.path.isdir(projects_dir):
        for dirpath, _, filenames in os.walk(projects_dir):
            for fname in filenames:
                if not fname.endswith(".jsonl"):
                    continue
                total_jsonls += 1
                session_id = fname[:-6]  # strip .jsonl
                if session_id not in live_sessions:
                    orphan_jsonls.append(os.path.join(dirpath, fname))

    print(f"Session JSONL files: {total_jsonls} total, {len(orphan_jsonls)} orphaned")
    total_size = 0
    for f in orphan_jsonls:
        sz = os.path.getsize(f)
        total_size += sz
        print(f"  {f}  ({sz / 1024:.1f} KB)")
        if not DRY_RUN:
            os.remove(f)

    print(f"  Total: {total_size / 1024 / 1024:.1f} MB")
    print()

    # 2. Scan output log files in /tmp
    orphan_logs = []
    total_logs = 0
    for log_path in glob.glob("/tmp/claude-output-*.log"):
        total_logs += 1
        # Extract message ID: /tmp/claude-output-{msg_id}.log
        basename = os.path.basename(log_path)
        msg_id = basename.replace("claude-output-", "").replace(".log", "")
        if msg_id not in live_msg_ids:
            orphan_logs.append(log_path)

    print(f"Output log files: {total_logs} total, {len(orphan_logs)} orphaned")
    log_size = 0
    for f in orphan_logs:
        sz = os.path.getsize(f)
        log_size += sz
        print(f"  {f}  ({sz / 1024:.1f} KB)")
        if not DRY_RUN:
            os.remove(f)

    print(f"  Total: {log_size / 1024 / 1024:.1f} MB")
    print()

    # 3. Clean up empty session directories
    removed_dirs = 0
    if os.path.isdir(projects_dir):
        for entry in os.listdir(projects_dir):
            d = os.path.join(projects_dir, entry)
            if os.path.isdir(d) and not os.listdir(d):
                print(f"  Empty dir: {d}")
                if not DRY_RUN:
                    os.rmdir(d)
                    removed_dirs += 1

    grand_total = total_size + log_size
    print(f"Summary: {len(orphan_jsonls)} session files + {len(orphan_logs)} log files = {grand_total / 1024 / 1024:.1f} MB")
    if DRY_RUN:
        print("\nThis was a dry run. Re-run with --delete to actually remove files.")
    else:
        print(f"\nDeleted {len(orphan_jsonls)} session files, {len(orphan_logs)} log files, {removed_dirs} empty dirs.")


if __name__ == "__main__":
    main()
