"""Backup status and manual runs. Plan section 14; Phase 10."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import scheduler
from app.config import settings
from app.db import get_session
from app.domain import backup

router = APIRouter()


class BackupFileOut(BaseModel):
    name: str
    written_at: datetime
    size_bytes: int


class BackupStatusOut(BaseModel):
    directory: str
    enabled: bool
    interval_hours: int
    keep: int
    #: None means none has ever been written -- a different problem from an old
    #: one, with a different fix.
    latest: BackupFileOut | None
    age_hours: float | None
    stale: bool
    last_error: str
    files: list[BackupFileOut]


def _file(f: backup.BackupFile) -> BackupFileOut:
    return BackupFileOut(
        name=f.name, written_at=f.written_at, size_bytes=f.size_bytes
    )


def _status() -> backup.BackupStatus:
    return backup.status(
        Path(settings.backup_dir),
        enabled=settings.backup_enabled,
        interval_hours=settings.backup_interval_hours,
        keep=settings.backup_keep,
        last_error=scheduler.last_error,
    )


def _out(s: backup.BackupStatus) -> BackupStatusOut:
    return BackupStatusOut(
        directory=s.directory,
        enabled=s.enabled,
        interval_hours=s.interval_hours,
        keep=s.keep,
        latest=_file(s.latest) if s.latest else None,
        age_hours=s.age_hours,
        stale=s.stale,
        last_error=s.last_error,
        files=[_file(f) for f in s.files],
    )


@router.get("/backups", response_model=BackupStatusOut)
def backup_status() -> BackupStatusOut:
    return _out(_status())


@router.post("/backups", response_model=BackupStatusOut, status_code=201)
def run_backup(session: Session = Depends(get_session)) -> BackupStatusOut:
    """Write one now. The same code path the timer uses."""
    try:
        backup.run_once(session, Path(settings.backup_dir), settings.backup_keep)
    except backup.BackupError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _out(_status())


@router.get("/backups/{name}")
def download_backup(name: str) -> FileResponse:
    """Fetch one off disk.

    The name is matched against the generated pattern rather than sanitised.
    A filter that tries to strip traversal is a filter someone eventually gets
    wrong; an allowlist of names this module itself produces cannot be walked.
    """
    if not backup.PATTERN.match(name):
        raise HTTPException(status_code=404, detail="no such backup")
    path = Path(settings.backup_dir) / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no such backup")
    return FileResponse(path, media_type="application/json", filename=name)
