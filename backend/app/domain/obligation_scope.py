"""Shared predicates for when an obligation still belongs in a forecast.

An automatic match is evidence, not authority. Until a person confirms it, the
safe failure is to keep reserving the commitment: dropping a wrongly matched bill
would overstate spendable cash. The budget projection separately removes linked
spend from its daily run rate, preventing that conservative reserve from turning
one bill into three through extrapolation.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import or_

from app.models.ledger import Transaction
from app.models.planning import ObligationInstance


def unresolved():
    """Instances with no match, or only an unconfirmed suggested match."""
    return or_(
        ObligationInstance.fulfilled_by_transaction_id.is_(None),
        ObligationInstance.match_confirmed.is_(False),
    )


def still_committed_as_of(as_of: date):
    """Instances whose money has not yet left cash on ``as_of``.

    The second half of O1: a future-dated transaction that fulfils a future
    obligation has moved nothing yet, so the commitment keeps counting until its
    booking date arrives. Dropping it early would let pre-recording next week's
    rent inflate today's safe-to-spend.

    The caller must already have ``outerjoin``-ed :class:`Transaction` on
    ``ObligationInstance.fulfilled_by_transaction_id`` -- this reads
    ``Transaction.booking_date``, and an inner join would silently drop every
    unmatched instance, which is the whole set this is meant to keep.
    """
    return or_(unresolved(), Transaction.booking_date > as_of)
