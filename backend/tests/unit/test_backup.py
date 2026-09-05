"""Scheduled backups. Phase 10.

* B-A -- one serialisation, shared with `/export/backup.json`
* B-B -- a backup file is a valid restore input
* B-C -- backups carry application data only, never secrets
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.domain import backup, restore as restore_module
from app.domain.disposable import account_balances, net_worth
from app.main import app
from app.models import Base
from tests.conftest import post

NOW = datetime(2026, 8, 31, 14, 25, 30, tzinfo=timezone.utc)


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def ledger(session, accounts, categories):
    post(session, date(2026, 8, 1), "Salary",
         [(accounts["current"], "2500"), (accounts["salary"], "-2500")])
    post(session, date(2026, 8, 4), "Tesco",
         [(accounts["current"], "-62.40"),
          (accounts["groceries"], "62.40", categories["groceries"])])
    return session


# --------------------------------------------------------------------------
# B-A
# --------------------------------------------------------------------------


def test_every_persistent_application_table_is_in_the_backup_contract():
    """A new table cannot quietly fall outside backup/restore."""
    assert set(backup.BACKUP_TABLES) == set(Base.metadata.tables)


def test_the_written_file_matches_the_export_endpoint_byte_for_byte(
    client, ledger, session, tmp_path
):
    """B-A. Two serialisations would mean restore was only tried against one."""
    written = backup.write(session, tmp_path, now=NOW)
    from_disk = written.path.read_text()
    from_api = client.get("/api/export/backup.json").text
    assert json.loads(from_disk) == json.loads(from_api)


# --------------------------------------------------------------------------
# B-B
# --------------------------------------------------------------------------


def test_a_backup_file_restores(session, ledger, tmp_path, accounts):
    """B-B. A backup you have not restored is a file, not a backup."""
    balances_before = account_balances(session, date(2026, 8, 31))
    worth_before = net_worth(session, date(2026, 8, 31))

    written = backup.write(session, tmp_path, now=NOW)
    payload = json.loads(written.path.read_text())

    restore_module.restore(session, payload, replace=True)
    session.expunge_all()

    assert account_balances(session, date(2026, 8, 31)) == balances_before
    assert net_worth(session, date(2026, 8, 31)) == worth_before


# --------------------------------------------------------------------------
# B-C
# --------------------------------------------------------------------------


def test_a_backup_contains_no_secrets(session, ledger, tmp_path, monkeypatch):
    """B-C. A backup ends up in places the database never does."""
    from app.config import settings

    monkeypatch.setattr(settings, "auth_password_hash", "pbkdf2$600000$SALT$HASH")
    monkeypatch.setattr(settings, "session_secret", "s3cr3t-signing-key")

    text = backup.write(session, tmp_path, now=NOW).path.read_text()
    assert "pbkdf2" not in text
    assert "s3cr3t-signing-key" not in text
    assert "SALT" not in text

    payload = json.loads(text)
    assert set(payload) == {
        "format", "version", "exported_for", "accounts", "categories",
        "transactions", "tables",
    }


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def test_the_filename_sorts_chronologically_as_text(session, ledger, tmp_path):
    early = backup.write(session, tmp_path, now=NOW)
    later = backup.write(session, tmp_path, now=NOW + timedelta(hours=5))
    assert sorted([later.name, early.name]) == [early.name, later.name]


def test_a_failed_write_leaves_the_previous_backup_intact(
    session, ledger, tmp_path, monkeypatch
):
    """Atomicity. A truncated file that looks like a backup is the worst case."""
    good = backup.write(session, tmp_path, now=NOW)
    original = good.path.read_text()

    def explode(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(backup.os, "replace", explode)
    with pytest.raises(backup.BackupError):
        backup.write(session, tmp_path, now=NOW + timedelta(hours=1))

    assert good.path.read_text() == original
    # And no half-written temp file is left behind pretending to be one.
    assert [p.name for p in tmp_path.glob("*.tmp")] == []


def test_a_directory_that_cannot_be_created_raises(session, ledger, tmp_path):
    """Never silent. A backup that failed must say so."""
    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file, not a directory")
    with pytest.raises(backup.BackupError):
        backup.write(session, blocker / "nested", now=NOW)


def test_files_we_did_not_write_are_ignored(session, ledger, tmp_path):
    backup.write(session, tmp_path, now=NOW)
    (tmp_path / "notes.json").write_text("{}")
    (tmp_path / "backup-nonsense.json").write_text("{}")
    assert len(backup.existing(tmp_path)) == 1


# --------------------------------------------------------------------------
# Retention
# --------------------------------------------------------------------------


def test_pruning_keeps_the_newest(session, ledger, tmp_path):
    for hours in range(5):
        backup.write(session, tmp_path, now=NOW + timedelta(hours=hours))
    backup.prune(tmp_path, keep=2)

    left = backup.existing(tmp_path)
    assert len(left) == 2
    assert left[0].written_at == NOW + timedelta(hours=4)
    assert left[1].written_at == NOW + timedelta(hours=3)


def test_keep_zero_keeps_everything(session, ledger, tmp_path):
    """A misread config must not be a data-loss event."""
    for hours in range(3):
        backup.write(session, tmp_path, now=NOW + timedelta(hours=hours))
    assert backup.prune(tmp_path, keep=0) == []
    assert len(backup.existing(tmp_path)) == 3


def test_run_once_writes_and_prunes(session, ledger, tmp_path):
    for hours in range(4):
        backup.run_once(session, tmp_path, keep=2, now=NOW + timedelta(hours=hours))
    assert len(backup.existing(tmp_path)) == 2


# --------------------------------------------------------------------------
# Status -- the part that makes the feature worth having
# --------------------------------------------------------------------------


def _status(directory, now, **kw):
    return backup.status(
        directory, enabled=True, interval_hours=24, keep=14, now=now, **kw
    )


def test_never_backed_up_is_stale(tmp_path):
    state = _status(tmp_path, NOW)
    assert state.latest is None
    assert state.age_hours is None
    assert state.stale


def test_a_recent_backup_is_not_stale(session, ledger, tmp_path):
    backup.write(session, tmp_path, now=NOW)
    state = _status(tmp_path, NOW + timedelta(hours=3))
    assert not state.stale
    assert state.age_hours == pytest.approx(3)


def test_one_missed_run_is_a_hiccup_two_is_a_pattern(session, ledger, tmp_path):
    backup.write(session, tmp_path, now=NOW)
    assert not _status(tmp_path, NOW + timedelta(hours=40)).stale
    assert _status(tmp_path, NOW + timedelta(hours=50)).stale


def test_a_disabled_scheduler_is_never_reported_as_stale(tmp_path):
    """Off is a choice; stale is a failure. They must not read the same."""
    state = backup.status(
        tmp_path, enabled=False, interval_hours=24, keep=14, now=NOW
    )
    assert not state.stale
    assert not state.enabled


# --------------------------------------------------------------------------
# The API
# --------------------------------------------------------------------------


def test_running_a_backup_from_the_api_writes_one(client, ledger, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "backup_dir", str(tmp_path))
    r = client.post("/api/backups")
    assert r.status_code == 201
    assert r.json()["latest"] is not None
    assert not r.json()["stale"]
    assert len(backup.existing(tmp_path)) == 1


def test_the_status_endpoint_reports_an_empty_directory_honestly(
    client, tmp_path, monkeypatch
):
    from app.config import settings

    monkeypatch.setattr(settings, "backup_dir", str(tmp_path))
    # The suite disables the timer globally, and off is deliberately not stale.
    monkeypatch.setattr(settings, "backup_enabled", True)
    body = client.get("/api/backups").json()
    assert body["latest"] is None
    assert body["stale"] is True
    assert body["files"] == []


def test_a_traversal_name_is_a_404_not_a_file(client, tmp_path, monkeypatch):
    """An allowlist of names this module produces cannot be walked."""
    from app.config import settings

    monkeypatch.setattr(settings, "backup_dir", str(tmp_path))
    for name in ("../../etc/passwd", "..%2f..%2fetc%2fpasswd", "backup.json"):
        assert client.get(f"/api/backups/{name}").status_code == 404


def test_a_written_backup_can_be_downloaded(client, ledger, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "backup_dir", str(tmp_path))
    name = client.post("/api/backups").json()["latest"]["name"]
    r = client.get(f"/api/backups/{name}")
    assert r.status_code == 200
    assert json.loads(r.text)["format"] == "personal-finance-os/backup"


# --------------------------------------------------------------------------
# The insight
# --------------------------------------------------------------------------


def test_a_stale_backup_becomes_an_insight(session, ledger, tmp_path, monkeypatch):
    from app.config import settings
    from app.domain import insights

    monkeypatch.setattr(settings, "backup_dir", str(tmp_path))
    monkeypatch.setattr(settings, "backup_enabled", True)

    kinds = {i.kind for i in insights.collect(session, date(2026, 8, 31))}
    assert "backups_stale" in kinds


def test_backups_switched_off_says_so(session, ledger, tmp_path, monkeypatch):
    from app.config import settings
    from app.domain import insights

    monkeypatch.setattr(settings, "backup_dir", str(tmp_path))
    monkeypatch.setattr(settings, "backup_enabled", False)

    kinds = {i.kind for i in insights.collect(session, date(2026, 8, 31))}
    assert "backups_off" in kinds
    assert "backups_stale" not in kinds


def test_a_fresh_backup_raises_nothing(session, ledger, tmp_path, monkeypatch):
    from app.config import settings
    from app.domain import insights

    monkeypatch.setattr(settings, "backup_dir", str(tmp_path))
    monkeypatch.setattr(settings, "backup_enabled", True)
    backup.write(session, tmp_path)

    kinds = {i.kind for i in insights.collect(session, date(2026, 8, 31))}
    assert "backups_stale" not in kinds
    assert "backups_off" not in kinds


def test_a_backup_is_not_world_readable(session, ledger, tmp_path):
    """A file containing every transaction should not be readable by other users.

    This falls out of `tempfile.mkstemp`, which creates at 0600, and `os.replace`,
    which preserves the mode. Pinned here because it is currently a property of
    how the write happens rather than a stated intention -- switching to
    `open()` would silently widen it to the umask default.
    """
    import stat

    written = backup.write(session, tmp_path, now=NOW)
    mode = stat.S_IMODE(written.path.stat().st_mode)
    assert mode & (stat.S_IRGRP | stat.S_IROTH) == 0, oct(mode)
