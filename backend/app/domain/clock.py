"""The single source of "today".

Rulebook section 9 / invariant D1. Every period boundary, budget window and
DaysRemaining calculation must agree on which day it is, and that day is defined
by the user's reporting timezone -- not by wherever the server happens to run.

``date.today()`` returns the *server's* local date. A server in Asia/Dubai rolls
over four hours before Europe/London, so for those four hours every budget would
be a day ahead of the user: DaysRemaining short by one, a period boundary crossed
early, and spending bucketed into tomorrow.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.planning import UserProfile

DEFAULT_TIMEZONE = "Europe/London"


def reporting_timezone(session: Session) -> ZoneInfo:
    """The user's configured reporting timezone, falling back to the default.

    An unknown timezone name falls back rather than raising: a bad settings value
    should not take the dashboard down.
    """
    profile = session.scalars(select(UserProfile)).first()
    name = profile.reporting_timezone if profile else DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def today(session: Session) -> date:
    """Today's date in the reporting timezone. Use this, never ``date.today()``."""
    return datetime.now(reporting_timezone(session)).date()
