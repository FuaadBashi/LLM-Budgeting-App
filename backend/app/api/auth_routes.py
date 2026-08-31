"""Login, logout, and the session check the frontend uses to decide what to show.

These are the only endpoints reachable without a session — everything else is
behind ``require_session``. Health is public too so a monitor can reach it.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel, Field

from app.auth import (
    SESSION_COOKIE,
    auth_enabled,
    clear_session,
    issue_session,
    session_is_valid,
    verify_password,
)
from app import ratelimit
from app.config import settings

router = APIRouter()

#: A deliberate delay on failure. Not a substitute for rate limiting, but it
#: makes an online guessing attack tedious rather than instant.
FAILED_ATTEMPT_DELAY_SECONDS = 0.5


class LoginIn(BaseModel):
    password: str = Field(min_length=1)


class SessionOut(BaseModel):
    #: False means the app is open — every route is reachable without a login.
    auth_enabled: bool
    authenticated: bool


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/auth/session", response_model=SessionOut)
def read_session(
    pfos_session: str | None = Cookie(default=None),
) -> SessionOut:
    """What the client needs to decide between the app and a login screen."""
    if not auth_enabled():
        return SessionOut(auth_enabled=False, authenticated=True)
    return SessionOut(auth_enabled=True, authenticated=session_is_valid(pfos_session))


@router.post("/auth/login", response_model=SessionOut)
def login(payload: LoginIn, response: Response) -> SessionOut:
    if not auth_enabled():
        # Nothing to log in to. Say so rather than pretending to accept it.
        return SessionOut(auth_enabled=False, authenticated=True)

    # Refused before the password is even checked, so a throttled attacker
    # learns nothing and costs nothing. 429 with Retry-After is the standard
    # shape and is what a client can actually act on.
    wait = ratelimit.check()
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"too many attempts; try again in {wait} seconds",
            headers={"Retry-After": str(wait)},
        )

    if not verify_password(payload.password, settings.auth_password_hash):
        ratelimit.record_failure()
        time.sleep(FAILED_ATTEMPT_DELAY_SECONDS)
        # No detail about which part was wrong -- there is only one field, and a
        # more specific message would only help someone guessing.
        raise HTTPException(status_code=401, detail="incorrect password")

    ratelimit.record_success()
    issue_session(response, secure=settings.cookie_secure)
    return SessionOut(auth_enabled=True, authenticated=True)


@router.post("/auth/logout", response_model=SessionOut)
def logout(response: Response) -> SessionOut:
    clear_session(response)
    return SessionOut(auth_enabled=auth_enabled(), authenticated=not auth_enabled())
