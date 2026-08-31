"""Login throttling and exposure headers. Phase 10.

The half-second delay that was here before said in its own comment that it was
not a substitute for rate limiting. At 0.5s an attempt that is roughly 170,000
guesses a day against one password.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import ratelimit
from app.auth import hash_password
from app.config import settings
from app.db import get_session
from app.main import app

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def _clean_throttle():
    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def auth_on(monkeypatch):
    """Auth switched on for this test only; the suite runs with it off."""
    monkeypatch.setattr(settings, "auth_password_hash", hash_password(PASSWORD))
    monkeypatch.setattr(settings, "session_secret", "test-signing-secret")


def wrong(client):
    return client.post("/api/auth/login", json={"password": "not-it"})


# --------------------------------------------------------------------------
# Backoff
# --------------------------------------------------------------------------


def test_a_typo_is_not_punished():
    """Fat-fingering a password twice is normal."""
    for _ in range(ratelimit.FREE_ATTEMPTS):
        ratelimit.record_failure()
    assert ratelimit.check() == 0


def test_the_delay_grows_after_the_free_attempts():
    for _ in range(ratelimit.FREE_ATTEMPTS + 1):
        ratelimit.record_failure()
    first = ratelimit.check()
    assert first > 0

    ratelimit.record_failure()
    assert ratelimit.check() > first, "each failure should cost more than the last"


def test_sustained_guessing_becomes_pointless():
    """The tenth attempt already waits minutes."""
    for _ in range(10):
        ratelimit.record_failure()
    assert ratelimit.check() >= 60


def test_the_owner_is_never_locked_out_permanently():
    """A single-user app locking 'the account' locks the only person using it."""
    for _ in range(100):
        ratelimit.record_failure()
    assert ratelimit.check() <= ratelimit.MAX_DELAY_SECONDS


def test_a_correct_password_clears_the_history():
    for _ in range(8):
        ratelimit.record_failure()
    assert ratelimit.check() > 0

    ratelimit.record_success()
    assert ratelimit.check() == 0


# --------------------------------------------------------------------------
# The endpoint
# --------------------------------------------------------------------------


def test_a_throttled_attempt_is_429_with_retry_after(client, auth_on):
    for _ in range(ratelimit.FREE_ATTEMPTS + 1):
        wrong(client)

    blocked = wrong(client)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0


def test_the_password_is_not_checked_while_throttled(client, auth_on, monkeypatch):
    """A throttled attacker must learn nothing, and cost nothing."""
    for _ in range(ratelimit.FREE_ATTEMPTS + 1):
        wrong(client)

    checked = []
    import app.api.auth_routes as routes

    monkeypatch.setattr(
        routes, "verify_password", lambda *a: checked.append(a) or False
    )
    client.post("/api/auth/login", json={"password": PASSWORD})
    assert checked == [], "a throttled request must not reach the hash at all"


def test_the_right_password_still_works_before_throttling(client, auth_on):
    r = client.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 200
    assert r.json()["authenticated"] is True


def test_a_wrong_password_still_says_only_that(client, auth_on):
    """One field, so a more specific message would only help a guesser."""
    r = wrong(client)
    assert r.status_code == 401
    assert r.json()["detail"] == "incorrect password"


def test_throttling_does_not_apply_when_auth_is_off(client):
    """Nothing to brute force, so nothing to throttle."""
    for _ in range(10):
        r = client.post("/api/auth/login", json={"password": "anything"})
        assert r.status_code == 200


# --------------------------------------------------------------------------
# Headers
# --------------------------------------------------------------------------


def test_the_baseline_headers_are_always_sent(client):
    r = client.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert r.headers["Content-Security-Policy"] == "default-src 'none'"


def test_hsts_is_not_sent_over_plain_http(client, monkeypatch):
    """Pinning a browser to a scheme this deployment does not serve would lock
    the owner out of their own finances -- worse than the hole it closes."""
    monkeypatch.setattr(settings, "cookie_secure", False)
    assert "Strict-Transport-Security" not in client.get("/api/health").headers


def test_hsts_is_sent_once_cookies_are_https_only(client, monkeypatch):
    monkeypatch.setattr(settings, "cookie_secure", True)
    header = client.get("/api/health").headers.get("Strict-Transport-Security")
    assert header and "max-age=31536000" in header


# --------------------------------------------------------------------------
# CORS
# --------------------------------------------------------------------------


def test_the_default_origin_is_the_local_dev_server():
    assert settings.allowed_origins == ["http://localhost:3000"]


def test_several_origins_can_be_configured(monkeypatch):
    monkeypatch.setattr(
        settings, "cors_origins", "https://money.example.com, http://localhost:3000"
    )
    assert settings.allowed_origins == [
        "https://money.example.com",
        "http://localhost:3000",
    ]


def test_blank_entries_are_dropped(monkeypatch):
    """A trailing comma in .env must not authorise an empty origin."""
    monkeypatch.setattr(settings, "cors_origins", "https://a.example.com,, ")
    assert settings.allowed_origins == ["https://a.example.com"]
