"""Shared predicates for when an obligation instance still belongs in a forecast.

Invariant O1: an instance affects forecasts only until its money has actually
left cash, and it must move from *committed* to *spent* in one step, never
appearing in both states or neither. The gate is therefore the fulfilment
**link**, not ``match_confirmed`` -- the moment a posted transaction is linked,
that money is already visible to every engine that reads the ledger, so leaving
the instance in the forecast subtracts the same payment twice.

Gating figures on ``match_confirmed`` was tried and produced exactly that: a paid
bill sat in Spent and in committed at once, the balance curve drew a second 600
drop for money already gone, and the run rate then extrapolated the same bill
across the rest of the month. What defends against a wrong link is strict,
unambiguous matching -- amount, date proximity, plus the obligation's declared
category and funding account -- and a durable unmatch action, not a flag nobody
has clicked yet.

``match_confirmed`` survives as a review flag, and drives exactly one thing: the
worklist the API serves to the match-review screen (:func:`awaiting_review`). It
must never become a term in a figure.

Extracted rather than repeated because two similar-looking queries over the same
money always drift, and the drift here is silent: safe-to-spend and the balance
curve would simply disagree about whether rent was still owed.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import and_, or_

from app.models.ledger import Transaction
from app.models.planning import ObligationInstance


def unmatched():
    """Instances no payment has been linked to at all.

    The forecast gate. The right predicate wherever the payment is already on
    the books by some other route -- the projection's Spent, the calendar's
    opening balance or its future-posted leg -- so the instance must not
    subtract the money a second time.
    """
    return ObligationInstance.fulfilled_by_transaction_id.is_(None)


def awaiting_review():
    """Instances a person still has something to do about.

    Unmatched bills, plus links the matcher guessed at that nobody has accepted.
    A worklist for the review screen, never a term in a figure: an unaccepted
    guess is still a link, and O1 has already taken it out of the forecasts.
    """
    return or_(
        unmatched(),
        and_(
            ObligationInstance.fulfilled_by_transaction_id.is_not(None),
            ObligationInstance.match_confirmed.is_(False),
        ),
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
    return or_(unmatched(), Transaction.booking_date > as_of)
