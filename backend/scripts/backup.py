#!/usr/bin/env python
"""Write one backup and prune. For cron, launchd, or a keyboard.

The API's own timer only runs while the API does. This is the same code path
with no such dependency:

    0 3 * * *  cd /path/to/backend && .venv/bin/python scripts/backup.py

Exits non-zero on failure so a scheduler can notice, and prints the path so a
log has something worth reading.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.domain import backup  # noqa: E402


def main() -> int:
    try:
        with SessionLocal() as session:
            written = backup.run_once(
                session, Path(settings.backup_dir), settings.backup_keep
            )
    except backup.BackupError as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"{written.path} ({written.size_bytes} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
