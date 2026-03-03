"""Server-side video thumbnail generation using ffmpeg."""

import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)

VIDEO_EXTS = {".mp4", ".webm", ".mov"}

# Match file paths ending in video extensions — same patterns as formatters.jsx
_VIDEO_EXT_LIST = "|".join(ext.lstrip(".") for ext in VIDEO_EXTS)
_RE_BARE_PATH = re.compile(
    r"(?:^|[\s(])([^\s()\[\]!]*/[^\s()\[\]]+\.(?:" + _VIDEO_EXT_LIST + r"))(?=[\s),\]]|$)",
    re.IGNORECASE | re.MULTILINE,
)
_RE_BACKTICK = re.compile(
    r"`([^`]*/[^`]*\.(?:" + _VIDEO_EXT_LIST + r"))`",
    re.IGNORECASE,
)


def is_video_file(path: str) -> bool:
    """Check if path has a video extension."""
    _, ext = os.path.splitext(path)
    return ext.lower() in VIDEO_EXTS


def thumb_path_for(video_path: str) -> str:
    """Return the thumbnail path for a given video file."""
    return video_path + ".thumb.jpg"


def generate_thumbnail(video_path: str) -> bool:
    """Generate a thumbnail for a video file using ffmpeg.

    Idempotent: skips if thumb exists and is newer than the video.
    Never raises — logs errors and returns False on failure.
    """
    try:
        if not os.path.isfile(video_path):
            return False

        output = thumb_path_for(video_path)

        # Skip if thumb exists and is newer than video
        if os.path.isfile(output):
            if os.path.getmtime(output) >= os.path.getmtime(video_path):
                return True

        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", "0.5",
                "-i", video_path,
                "-vframes", "1",
                "-vf", "scale=320:-1",
                "-q:v", "5",
                output,
            ],
            timeout=30,
            capture_output=True,
        )

        if os.path.isfile(output) and os.path.getsize(output) > 0:
            logger.debug("Generated thumbnail: %s", output)
            return True
        else:
            logger.warning("ffmpeg produced no output for %s", video_path)
            return False

    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg timed out for %s", video_path)
        return False
    except FileNotFoundError:
        logger.warning("ffmpeg not found — cannot generate video thumbnails")
        return False
    except Exception:
        logger.warning("Thumbnail generation failed for %s", video_path, exc_info=True)
        return False


def generate_thumbnails_for_message(content: str, project_path: str) -> None:
    """Extract video paths from message text and generate thumbnails.

    Scans for paths matching video extensions and generates .thumb.jpg
    files next to each video. All errors are logged, never propagated.
    """
    if not content or not project_path:
        return

    try:
        paths: set[str] = set()

        for m in _RE_BARE_PATH.finditer(content):
            paths.add(m.group(1))
        for m in _RE_BACKTICK.finditer(content):
            paths.add(m.group(1))

        for raw_path in paths:
            # Resolve relative to project_path
            if os.path.isabs(raw_path):
                full_path = raw_path
            else:
                full_path = os.path.join(project_path, raw_path)

            full_path = os.path.normpath(full_path)

            if os.path.isfile(full_path):
                generate_thumbnail(full_path)

    except Exception:
        logger.warning(
            "generate_thumbnails_for_message failed", exc_info=True,
        )


def backfill_thumbnails() -> None:
    """Scan all agent messages in the DB and generate missing thumbnails.

    Intended to run once at startup in a background thread.
    """
    try:
        from database import SessionLocal
        from models import Message, MessageRole, Project

        db = SessionLocal()
        try:
            # Build project path lookup
            projects = {p.name: p.path for p in db.query(Project).all()}

            messages = db.query(Message).filter(
                Message.role == MessageRole.AGENT,
                Message.content.isnot(None),
            ).all()

            count = 0
            for msg in messages:
                # Get project path from agent
                from models import Agent
                agent = db.get(Agent, msg.agent_id)
                if not agent or agent.project not in projects:
                    continue
                project_path = projects[agent.project]

                paths: set[str] = set()
                for m in _RE_BARE_PATH.finditer(msg.content):
                    paths.add(m.group(1))
                for m in _RE_BACKTICK.finditer(msg.content):
                    paths.add(m.group(1))

                for raw_path in paths:
                    if os.path.isabs(raw_path):
                        full_path = raw_path
                    else:
                        full_path = os.path.join(project_path, raw_path)
                    full_path = os.path.normpath(full_path)
                    if os.path.isfile(full_path) and not os.path.isfile(thumb_path_for(full_path)):
                        if generate_thumbnail(full_path):
                            count += 1
        finally:
            db.close()

        if count:
            logger.info("Backfilled %d video thumbnails", count)

    except Exception:
        logger.warning("backfill_thumbnails failed", exc_info=True)
