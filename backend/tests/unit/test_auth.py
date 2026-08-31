"""Single-user access control. Plan section 14.

Auth is off unless a password hash is configured, so most of the suite runs
against an open app. These tests configure one and check the door actually shuts.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.auth import hash_password, verify_password
from app.config import settings
from app.db import get_session
from app.main import app

PASSWORD = "a-sufficiently-long-passphrase"


@pytest.fixture
def open_client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def secured_client(session, monkeypatch):
    """An app with a password configured."""
    monkeypatch.setattr(settings, "auth_password_hash", hash_password(PASSWORD))
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------


def test_the_password_is_not_recoverable_from_the_hash():
    encoded = hash_password(PASSWORD)
    assert PASSWORD not in encoded
    assert encoded.startswith("pbkdf2$")


def test_verification_round_trips():
    encoded = hash_password(PASSWORD)
    assert verify_password(PASSWORD, encoded)
    assert not verify_password(PASSWORD + "x", encoded)
    assert not verify_password("", encoded)


def test_the_same_password_hashes_differently_each_time():
    """Per-hash salt: identical passwords must not produce identical hashes."""
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_a_malformed_hash_is_rejected_not_crashed():
    for bad in ["", "nonsense", "pbkdf2$only$three", "bcrypt$1$a$b"]:
        assert verify_password(PASSWORD, bad) is False


# --------------------------------------------------------------------------
# Open by default
# --------------------------------------------------------------------------


def test_without_a_password_the_app_is_open(open_client):
    assert open_client.get("/api/accounts").status_code == 200
    body = open_client.get("/api/auth/session").json()
    assert body == {"auth_enabled": False, "authenticated": True}


def test_startup_warns_when_unprotected(monkeypatch):
    """The insecure default must announce itself rather than be implied."""
    monkeypatch.setattr(settings, "auth_password_hash", "")
    monkeypatch.delenv("PFOS_ALLOW_INSECURE", raising=False)
    assert "AUTH IS DISABLED" in (auth.startup_warning() or "")


def test_no_warning_once_a_password_is_set(monkeypatch):
    monkeypatch.setattr(settings, "auth_password_hash", hash_password(PASSWORD))
    assert auth.startup_warning() is None


# --------------------------------------------------------------------------
# Closed when configured
# --------------------------------------------------------------------------


def test_protected_routes_reject_an_anonymous_request(secured_client):
    for path in [
        "/api/accounts",
        "/api/transactions",
        "/api/dashboard/safe-to-spend",
        "/api/dashboard/budgets",
        "/api/obligations",
        "/api/analytics/period",
        "/api/export/backup.json",
    ]:
        assert secured_client.get(path).status_code == 401, path


def test_health_stays_public(secured_client):
    """A monitor should not need a password to see the process is alive."""
    assert secured_client.get("/api/health").status_code == 200


def test_login_then_access(secured_client):
    assert secured_client.get("/api/accounts").status_code == 401

    r = secured_client.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 200
    assert r.json()["authenticated"] is True

    assert secured_client.get("/api/accounts").status_code == 200


def test_a_wrong_password_is_refused(secured_client):
    r = secured_client.post("/api/auth/login", json={"password": "wrong-password"})
    assert r.status_code == 401
    assert secured_client.get("/api/accounts").status_code == 401


def test_logout_ends_the_session(secured_client):
    secured_client.post("/api/auth/login", json={"password": PASSWORD})
    assert secured_client.get("/api/accounts").status_code == 200

    secured_client.post("/api/auth/logout")
    assert secured_client.get("/api/accounts").status_code == 401


def test_the_session_cookie_is_not_readable_by_javascript(secured_client):
    r = secured_client.post("/api/auth/login", json={"password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_a_forged_cookie_is_rejected(secured_client):
    """The signature is what makes the cookie a credential rather than a claim."""
    secured_client.cookies.set("pfos_session", "eyJleHAiOjk5OTk5OTk5OTl9.forged")
    assert secured_client.get("/api/accounts").status_code == 401


def test_writes_are_protected_too(secured_client, accounts):
    """A read-only guard would leave the ledger writable by anyone."""
    body = {
        "booking_date": "2026-08-15",
        "description": "Should not be recorded",
        "postings": [
            {"account_id": str(accounts["current"].id), "amount_minor": -100},
            {"account_id": str(accounts["groceries"].id), "amount_minor": 100},
        ],
    }
    assert secured_client.post("/api/transactions", json=body).status_code == 401


def test_changing_the_password_invalidates_existing_sessions(
    secured_client, monkeypatch
):
    """The signing key is derived from the hash, so a change revokes sessions."""
    secured_client.post("/api/auth/login", json={"password": PASSWORD})
    assert secured_client.get("/api/accounts").status_code == 200

    monkeypatch.setattr(settings, "auth_password_hash", hash_password("a-different-passphrase"))
    assert secured_client.get("/api/accounts").status_code == 401
