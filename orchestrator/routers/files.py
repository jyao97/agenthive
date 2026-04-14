"""File serving routes — project files, thumbnails, uploads."""

import asyncio
import logging
import os
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from config import UPLOADS_DIR
from database import get_db
from models import Project

logger = logging.getLogger("orchestrator")

router = APIRouter(tags=["files"])

_THUMB_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _serve_file_with_range(full_path: str, media_type: str, request: Request):
    """Return a FileResponse with built-in Range request support."""
    return FileResponse(full_path, media_type=media_type)


def _resolve_project_file(project: str, path: str, db) -> str:
    """Resolve a project-relative path to an absolute filesystem path.

    Raises HTTPException(404) if the file cannot be found.
    """
    proj = db.get(Project, project)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")

    base_dir = os.path.realpath(proj.path)
    base_name = os.path.basename(base_dir)

    # Normalise the requested path
    clean = path
    _dbl = re.match(r"api/files/[^/]+/(.+)", clean)
    if _dbl:
        clean = _dbl.group(1)
    if clean.startswith(base_dir + "/"):
        clean = clean[len(base_dir) + 1:]
    elif clean.startswith(base_name + "/"):
        clean = clean[len(base_name) + 1:]

    full_path = os.path.realpath(os.path.join(base_dir, clean))
    if not full_path.startswith(base_dir + os.sep):
        # Path may be an absolute path with leading / stripped by the URL router.
        # Reconstruct it and check if it falls inside any registered project dir.
        abs_candidate = os.path.realpath("/" + clean)
        resolved_abs = False
        for p in db.query(Project).all():
            p_base = os.path.realpath(p.path)
            if abs_candidate.startswith(p_base + os.sep) and os.path.isfile(abs_candidate):
                full_path = abs_candidate
                resolved_abs = True
                break
        if not resolved_abs:
            raise HTTPException(status_code=400, detail="Invalid path")

    # Fallback: try the original path as-is if normalised version doesn't exist
    if not os.path.isfile(full_path):
        fallback = os.path.realpath(os.path.join(base_dir, path))
        if fallback.startswith(base_dir + os.sep) and os.path.isfile(fallback):
            full_path = fallback
        else:
            # Fallback 2: walk one level of subdirectories
            found = False
            for entry in os.listdir(base_dir):
                sub = os.path.join(base_dir, entry)
                if not os.path.isdir(sub):
                    continue
                candidate = os.path.realpath(os.path.join(sub, clean))
                if candidate.startswith(base_dir + os.sep) and os.path.isfile(candidate):
                    full_path = candidate
                    found = True
                    break
            # Fallback 3: search all registered project directories
            if not found:
                for other in db.query(Project).filter(Project.name != project).all():
                    other_base = os.path.realpath(other.path)
                    for root_candidate in [other_base]:
                        candidate = os.path.realpath(os.path.join(root_candidate, clean))
                        if candidate.startswith(other_base + os.sep) and os.path.isfile(candidate):
                            full_path = candidate
                            found = True
                            break
                        if os.path.isdir(root_candidate):
                            for entry in os.listdir(root_candidate):
                                sub = os.path.join(root_candidate, entry)
                                if not os.path.isdir(sub):
                                    continue
                                candidate = os.path.realpath(os.path.join(sub, clean))
                                if candidate.startswith(other_base + os.sep) and os.path.isfile(candidate):
                                    full_path = candidate
                                    found = True
                                    break
                        if found:
                            break
                    if found:
                        break

            if not found:
                raise HTTPException(status_code=404, detail="File not found")

    return full_path


def _write_bytes(path: str, data: bytes):
    with open(path, "wb") as f:
        f.write(data)


@router.get("/api/files/{project}/{path:path}")
async def serve_project_file(project: str, path: str, request: Request,
                             download: bool = False, db: Session = Depends(get_db)):
    """Serve a file from a project's directory (images, videos, etc.)."""
    import mimetypes
    full_path = _resolve_project_file(project, path, db)
    media_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
    if download:
        return FileResponse(full_path, media_type=media_type,
                            filename=os.path.basename(full_path))
    return _serve_file_with_range(full_path, media_type, request)


@router.get("/api/thumbs/{project}/{path:path}")
async def serve_thumbnail(project: str, path: str, request: Request, db: Session = Depends(get_db)):
    """Serve a resized thumbnail (max 1200px, JPEG q80) with filesystem caching."""
    import mimetypes
    full_path = _resolve_project_file(project, path, db)

    _, ext = os.path.splitext(full_path)
    if ext.lower() not in _THUMB_IMAGE_EXTS:
        media_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
        return _serve_file_with_range(full_path, media_type, request)

    # Cache path: .thumbcache/<filename>.thumb.jpg next to the original
    cache_dir = os.path.join(os.path.dirname(full_path), ".thumbcache")
    thumb_file = os.path.join(cache_dir, os.path.basename(full_path) + ".thumb.jpg")

    if os.path.isfile(thumb_file) and os.path.getmtime(thumb_file) >= os.path.getmtime(full_path):
        return FileResponse(thumb_file, media_type="image/jpeg")

    try:
        from PIL import Image
        os.makedirs(cache_dir, exist_ok=True)
        with Image.open(full_path) as img:
            img.thumbnail((1200, 1200))
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            img.save(thumb_file, "JPEG", quality=80)
        return FileResponse(thumb_file, media_type="image/jpeg")
    except (ImportError, IOError, OSError, ValueError) as e:
        logger.debug("Thumbnail generation failed for %s: %s", full_path, e)
        media_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
        return _serve_file_with_range(full_path, media_type, request)


@router.post("/api/upload")
async def upload_file(request: Request):
    """Upload a file (multipart form data). Returns filename, original_name, path, size."""
    from uuid import uuid4
    from fastapi import UploadFile, File

    form = await request.form()
    file: UploadFile = form.get("file")
    if not file:
        raise HTTPException(status_code=400, detail="No file provided")

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File exceeds 50 MB limit")

    # Sanitize original filename
    original_name = os.path.basename(file.filename or "upload")
    original_name = re.sub(r'[^\w.\- ]', '_', original_name)
    unique_name = f"{uuid4().hex[:12]}_{original_name}"

    os.makedirs(UPLOADS_DIR, exist_ok=True)
    dest = os.path.join(UPLOADS_DIR, unique_name)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: _write_bytes(dest, content))

    return {
        "filename": unique_name,
        "original_name": original_name,
        "path": dest,
        "size": len(content),
    }


@router.get("/api/uploads/{filename}")
async def serve_upload(filename: str, request: Request):
    """Serve an uploaded file."""
    import mimetypes
    safe_name = os.path.basename(filename)
    full_path = os.path.join(UPLOADS_DIR, safe_name)
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="File not found")
    media_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
    return _serve_file_with_range(full_path, media_type, request)
