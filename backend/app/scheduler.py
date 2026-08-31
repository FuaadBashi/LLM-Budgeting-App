"""The backup timer. Phase 10.

An asyncio task started by the FastAPI lifespan, not APScheduler. One periodic
job does not justify a dependency, and the lifespan task is the idiomatic
FastAPI answer for exactly this shape of work.

**The limitation is real and worth stating rather than discovering:** this only
runs while the API does. If the process is down for a week, no backup is taken
that week. That is why `status()` reports staleness and why the insights engine
surfaces it -- the timer is the convenience, and the visible age of the newest
file is the actual safety mechanism. For a backup that runs regardless, wire
`scripts/backup.py` into cron or launchd; the API's own timer then simply
finds a recent file and does nothing.

Failures are recorded and reported, never swallowed. A backup system that fails
quietly is worse than none, because it is trusted.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.db import SessionLocal
from app.domain import backup
from app.domain.clock import now as utc_now

log = logging.getLogger("uvicorn.error")

#: Set by the timer so `/backups` can report a failure the user never saw.
last_error: str = ""
last_run: datetime | None = None


def _run() -> None:
    """One backup, in a session of its own. Synchronous; called in a thread."""
    global last_error, last_run
    with SessionLocal() as session:
        written = backup.run_once(
            session, Path(settings.backup_dir), settings.backup_keep
        )
    last_error = ""
    last_run = utc_now()
    log.info("backup written: %s (%d bytes)", written.name, written.size_bytes)


async def _loop() -> None:
    interval = max(1, settings.backup_interval_hours) * 3600
    while True:
        try:
            # The write is blocking I/O plus a full table scan; off the event
            # loop so a large ledger does not stall every request for its
            # duration.
            await asyncio.to_thread(_run)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- a timer must not die
            global last_error
            last_error = str(exc)
            log.warning("scheduled backup failed: %s", exc)
        await asyncio.sleep(interval)


def start() -> asyncio.Task | None:
    """Begin the timer, if it is switched on."""
    if not settings.backup_enabled:
        log.warning(
            "Scheduled backups are off. Exports still work, but nothing is "
            "written automatically -- set BACKUP_ENABLED=true to turn them on."
        )
        return None
    return asyncio.create_task(_loop(), name="scheduled-backup")


async def stop(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
