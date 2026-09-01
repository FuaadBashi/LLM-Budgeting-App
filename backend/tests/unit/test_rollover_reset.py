"""``rollover_reset``: a write-off must survive an unrelated edit. Invariant B8.

Budgets are effective-dated. A ``BudgetRevision`` is the plan in force from a
date, so a closed period keeps the answer that was in force while it ran, and a
PATCH is a partial edit of that plan -- it changes the fields it names and leaves
the rest alone.

``rollover_reset`` broke that. It was declared ``bool = False`` and applied
unconditionally, so any later edit landing on the same revision resent False:
forgive £400 of carried overspend in September, bump the amount afterwards, and
the £400 is back with nothing on screen to say so.

The engine side is the other half of the claim: the reset belongs to the
revision, not to one period.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.domain.budgets import chain
from app.main import app
from app.models import Budget, BudgetPeriod, BudgetRevision, RolloverPolicy
from tests.conftest import post


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


START = date(2026, 7, 1)
SEPTEMBER = date(2026, 9, 1)


def make_budget(session, *, amount="500", policy=RolloverPolicy.FULL) -> Budget:
    """£500 monthly from July, carrying deficits in full."""
    b = Budget(name="Groceries", period=BudgetPeriod.MONTHLY, start_date=START)
    session.add(b)
    session.flush()
    session.add(
        BudgetRevision(
            budget_id=b.id,
            effective_from=START,
            amount=Decimal(amount),
            rollover_policy=policy,
        )
    )
    session.commit()
    session.refresh(b)
    return b


def revise(session, budget, when, *, amount="500", reset=False) -> BudgetRevision:
    rev = BudgetRevision(
        budget_id=budget.id,
        effective_from=when,
        amount=Decimal(amount),
        rollover_policy=RolloverPolicy.FULL,
        rollover_reset=reset,
    )
    session.add(rev)
    session.commit()
    session.refresh(budget)
    return rev


def spend(session, accounts, when, amount):
    post(
        session,
        when,
        "spend",
        [(accounts["current"], f"-{amount}"), (accounts["groceries"], amount)],
    )


def overspent_july_and_august(session, accounts) -> Budget:
    """July ends £300 up; August overspends and carries −£400 into September."""
    b = make_budget(session)
    spend(session, accounts, date(2026, 7, 10), "200")
    spend(session, accounts, date(2026, 8, 10), "1200")
    return b


def by_month(results) -> dict:
    return {r.period_start.month: r for r in results}


def periods(session, budget, upto=date(2026, 11, 30)) -> dict:
    return by_month(chain(session, budget, upto, upto))


# --------------------------------------------------------------------------
# The PATCH contract: an edit changes what it names
# --------------------------------------------------------------------------


def create_via_api(client) -> str:
    r = client.post(
        "/api/budgets",
        json={
            "name": "Groceries",
            "period": "monthly",
            "start_date": START.isoformat(),
            "amount_minor": 50000,
            "rollover_policy": "full",
        },
    )
    assert r.status_code == 201
    return r.json()["id"]


def revision_on(session, budget_id, when) -> BudgetRevision:
    session.expire_all()
    budget = session.get(Budget, budget_id)
    return next(r for r in budget.revisions if r.effective_from == when)


def test_changing_the_amount_does_not_clear_an_existing_reset(client, session):
    """The bug: the second edit never mentions the reset, so it must not touch it.

    Clearing it would resurrect the carried overspend the first edit wrote off,
    changing a figure the user was shown without ever saying so.
    """
    budget_id = create_via_api(client)
    client.patch(
        f"/api/budgets/{budget_id}",
        json={
            "amount_minor": 50000,
            "rollover_reset": True,
            "effective_from": SEPTEMBER.isoformat(),
        },
    )

    r = client.patch(
        f"/api/budgets/{budget_id}",
        json={"amount_minor": 60000, "effective_from": SEPTEMBER.isoformat()},
    )
    assert r.status_code == 200

    rev = revision_on(session, budget_id, SEPTEMBER)
    assert rev.amount == Decimal("600")
    assert rev.rollover_reset is True


def test_sending_false_explicitly_clears_the_reset(client, session):
    """Undoing a write-off stays possible -- it just has to be asked for."""
    budget_id = create_via_api(client)
    client.patch(
        f"/api/budgets/{budget_id}",
        json={
            "amount_minor": 50000,
            "rollover_reset": True,
            "effective_from": SEPTEMBER.isoformat(),
        },
    )

    client.patch(
        f"/api/budgets/{budget_id}",
        json={
            "amount_minor": 50000,
            "rollover_reset": False,
            "effective_from": SEPTEMBER.isoformat(),
        },
    )

    assert revision_on(session, budget_id, SEPTEMBER).rollover_reset is False


def test_a_later_revision_does_not_inherit_the_reset(client, session):
    """The policy is a standing setting and inherits; a reset is not and does not.

    An inherited reset would re-fire at every subsequent revision, so each edit
    would silently throw away the surplus accumulated since the write-off.
    """
    budget_id = create_via_api(client)
    client.patch(
        f"/api/budgets/{budget_id}",
        json={
            "amount_minor": 50000,
            "rollover_reset": True,
            "effective_from": SEPTEMBER.isoformat(),
        },
    )

    client.patch(
        f"/api/budgets/{budget_id}",
        json={"amount_minor": 60000, "effective_from": "2026-10-01"},
    )

    october = revision_on(session, budget_id, date(2026, 10, 1))
    assert october.rollover_reset is False
    assert october.rollover_policy is RolloverPolicy.FULL
    # And September keeps its own.
    assert revision_on(session, budget_id, SEPTEMBER).rollover_reset is True


def test_an_edit_inherits_the_plan_in_force_at_its_own_date(client, session):
    """Inheritance reads the revision in force at ``effective_from``, not the
    latest one.

    Taking the latest lets a revision dated *after* the edit reach backwards. A
    pause scheduled for December, plus a backdated August amount correction, gave
    August ``active=False`` -- and the engine drops paused periods entirely, so
    an amount edit deleted August through November from the chain.
    """
    budget_id = create_via_api(client)
    r = client.patch(
        f"/api/budgets/{budget_id}",
        json={
            "amount_minor": 50000,
            "active": False,
            "rollover_policy": "none",
            "effective_from": "2026-12-01",
        },
    )
    assert r.status_code == 200

    r = client.patch(
        f"/api/budgets/{budget_id}",
        json={"amount_minor": 60000, "effective_from": "2026-08-01"},
    )
    assert r.status_code == 200

    august = revision_on(session, budget_id, date(2026, 8, 1))
    assert august.active is True
    assert august.rollover_policy is RolloverPolicy.FULL
    # And December keeps what it was actually given.
    december = revision_on(session, budget_id, date(2026, 12, 1))
    assert december.active is False
    assert december.rollover_policy is RolloverPolicy.NONE


def test_a_scheduled_pause_does_not_delete_months_from_the_chain(client, session):
    """The engine-side consequence of the above, stated independently.

    Every month from July to November is still a live period after an amount
    edit; the pause takes effect in December, where it was asked for.
    """
    budget_id = create_via_api(client)
    client.patch(
        f"/api/budgets/{budget_id}",
        json={"amount_minor": 50000, "active": False, "effective_from": "2026-12-01"},
    )
    client.patch(
        f"/api/budgets/{budget_id}",
        json={"amount_minor": 60000, "effective_from": "2026-08-01"},
    )

    session.expire_all()
    budget = session.get(Budget, budget_id)
    assert sorted(periods(session, budget)) == [7, 8, 9, 10, 11]


def test_changing_the_amount_does_not_resume_a_paused_budget(client, session):
    """The same contract, for the other standing setting on the revision.

    ``active`` is a standing setting like the policy, so a new revision must
    inherit it. Defaulting it to True meant an amount edit resumed a paused
    budget: the engine drops paused periods from the chain entirely, so August
    onwards would reappear, each carrying its rollover, on a budget the user had
    switched off and with nothing on screen to say so.
    """
    budget_id = create_via_api(client)
    r = client.patch(
        f"/api/budgets/{budget_id}",
        json={"amount_minor": 50000, "active": False, "effective_from": "2026-08-01"},
    )
    assert r.status_code == 200

    r = client.patch(
        f"/api/budgets/{budget_id}",
        json={"amount_minor": 60000, "effective_from": SEPTEMBER.isoformat()},
    )
    assert r.status_code == 200

    assert revision_on(session, budget_id, SEPTEMBER).active is False
    # And the pause still holds in the engine: July is the only live period, not
    # July plus the four months the resumed budget would have put back.
    budget = session.get(Budget, budget_id)
    assert [r.period_start.month for r in periods(session, budget).values()] == [7]


# --------------------------------------------------------------------------
# What the flag does to the chain
# --------------------------------------------------------------------------


def test_a_reset_zeroes_the_carry_from_its_own_period(session, accounts):
    b = overspent_july_and_august(session, accounts)
    assert periods(session, b)[9].rollover_in == Decimal("-400")

    revise(session, b, SEPTEMBER, reset=True)

    september = periods(session, b)[9]
    assert september.rollover_in == Decimal("0")
    # £100 is the un-forgiven answer (£500 − £400), and the number that comes
    # back if an unrelated edit clears the flag.
    assert september.remaining == Decimal("500")


def test_periods_before_the_reset_keep_their_original_carry(session, accounts):
    """Effective dating: a September revision cannot reach into August.

    Zeroing the carry everywhere rather than from ``effective_from`` would erase
    July's £300 surplus from August, which is history being rewritten.
    """
    b = overspent_july_and_august(session, accounts)
    before = {m: (r.rollover_in, r.remaining) for m, r in periods(session, b).items()}

    revise(session, b, SEPTEMBER, reset=True)
    after = periods(session, b)

    for month in (7, 8):
        assert (after[month].rollover_in, after[month].remaining) == before[month]
    assert after[8].rollover_in == Decimal("300")
    assert after[8].remaining == Decimal("-400")


def test_a_reset_forgives_once_and_then_rollover_resumes(session, accounts):
    """The reset is a one-shot write-off at its boundary, not a standing setting.

    This test previously asserted the opposite — that the reset re-fired for
    every period its revision governed, which meant September's £500 surplus,
    and every surplus after it, silently vanished for the life of the budget. A
    user who wrote off £400 of carried overspend would have lost rollover
    permanently without anything on screen saying so.

    Permanent suspension is already expressible as rollover_policy = NONE, chosen
    deliberately and visible in the budget's settings. A one-time act of
    forgiveness must not also be a hidden, sticky change of policy.
    """
    b = overspent_july_and_august(session, accounts)
    revise(session, b, SEPTEMBER, reset=True)
    got = periods(session, b)

    # September opens at zero: that is the write-off doing its job.
    assert got[9].rollover_in == Decimal("0")
    assert got[9].remaining == Decimal("500")

    # And then rollover resumes. September spent nothing, so its £500 surplus
    # carries into October, and October's into November. £0 in either month
    # would be the old re-firing behaviour.
    assert got[10].rollover_in == Decimal("500")
    assert got[10].remaining == Decimal("1000")
    assert got[11].rollover_in == Decimal("1000")
    assert got[11].remaining == Decimal("1500")


def test_the_write_off_does_not_reach_back_past_its_own_boundary(session, accounts):
    """Forgiving September must not also forgive August's overspend."""
    b = overspent_july_and_august(session, accounts)
    revise(session, b, SEPTEMBER, reset=True)
    got = periods(session, b)

    assert got[8].remaining == Decimal("-400"), "August keeps what it actually did"
    assert got[7].rollover_in == Decimal("0")


def test_the_written_off_carry_is_reported_not_swallowed(session, accounts):
    """The one operation whose whole purpose is forgiveness must say how much.

    `carry()` reports what a policy forgives on EXIT. A reset forgives on ENTRY,
    which went through a different code path and was reported nowhere — so the
    carry simply vanished with no figure naming it.
    """
    b = overspent_july_and_august(session, accounts)
    revise(session, b, SEPTEMBER, reset=True)
    got = periods(session, b)

    # August ended at -400, so that is what September's reset wrote off.
    assert got[9].rollover_forgiven == Decimal("-400")
    assert got[9].rollover_in == Decimal("0")
    # And it is reported once, not again in the periods that follow.
    assert got[10].rollover_forgiven == Decimal("0")


def test_a_mid_period_revision_is_refused_rather_than_deferred(client, session, accounts):
    """Accepted-and-ignored is the failure this codebase refuses everywhere."""
    b = overspent_july_and_august(session, accounts)
    r = client.patch(
        f"/api/budgets/{b.id}",
        json={"amount_minor": 50000, "effective_from": "2026-09-15", "rollover_reset": True},
    )
    assert r.status_code == 422
    assert "inside a period" in r.json()["detail"]
    assert "2026-09-01" in r.json()["detail"], "it must name the boundary to use"


def test_a_period_boundary_is_still_accepted(client, session, accounts):
    b = overspent_july_and_august(session, accounts)
    r = client.patch(
        f"/api/budgets/{b.id}",
        json={"amount_minor": 50000, "effective_from": "2026-09-01", "rollover_reset": True},
    )
    assert r.status_code == 200
