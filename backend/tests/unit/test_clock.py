"""Invariant D1: "today" comes from the reporting timezone, not the server.

Rulebook section 9. These tests pin the behaviour that a server running in a
different timezone from the user cannot shift the user's day.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.domain.clock import DEFAULT_TIMEZONE, reporting_timezone, today
from app.models import UserProfile


def test_defaults_to_europe_london_without_a_profile(session):
    assert reporting_timezone(session) == ZoneInfo(DEFAULT_TIMEZONE)


def test_uses_the_configured_timezone(session):
    session.add(UserProfile(reporting_timezone="Asia/Dubai"))
    session.commit()
    assert reporting_timezone(session) == ZoneInfo("Asia/Dubai")


def test_unknown_timezone_falls_back_rather_than_raising(session):
    """A bad settings value must not take the dashboard down."""
    session.add(UserProfile(reporting_timezone="Mars/Olympus_Mons"))
    session.commit()
    assert reporting_timezone(session) == ZoneInfo(DEFAULT_TIMEZONE)


def test_server_timezone_does_not_shift_the_users_day(session):
    """The actual bug: a +04 server at 01:30 is already on the next day.

    London is still on the previous date, and the user's budgets, DaysRemaining
    and period boundaries must follow London.
    """
    session.add(UserProfile(reporting_timezone="Europe/London"))
    session.commit()

    # 2026-08-31 01:30 in Dubai is 2026-08-30 22:30 in London -- a different date,
    # and a different month.
    dubai_instant = datetime(2026, 8, 31, 1, 30, tzinfo=ZoneInfo("Asia/Dubai"))

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return dubai_instant.astimezone(tz)

    with patch("app.domain.clock.datetime", FrozenDatetime):
        assert today(session) == date(2026, 8, 30)

    # Sanity: the naive server-local answer would have been the 31st.
    assert dubai_instant.date() == date(2026, 8, 31)


def test_safe_to_spend_uses_the_reporting_day(session, accounts):
    """compute_safe_to_spend with no explicit date must go through the clock."""
    from app.domain.disposable import compute_safe_to_spend

    session.add(UserProfile(reporting_timezone="Europe/London",
                            protected_cash_buffer=Decimal("0")))
    session.commit()

    with patch("app.domain.disposable.clock_today", return_value=date(2026, 8, 30)):
        result = compute_safe_to_spend(session)

    # 30 days of fallback window from the injected day, not from the server's day.
    assert result.window_end == date(2026, 9, 29)
