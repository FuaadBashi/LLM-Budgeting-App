"""Category scope resolution for budgets. Rulebook section 8.

A budget scoped to "Food" must count its children -- Groceries, Restaurants,
Takeaway -- because leaf categories are what transactions actually carry. Exact
matching gives Spent = £0 and a permanently full budget, and it fails more badly
the more carefully the user builds their taxonomy.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.enums import CategoryNature
from app.models.ledger import Account, Posting

#: Hard stop on descent depth. A CHECK forbids a self-parent, but a longer cycle
#: (Food -> Snacks -> Food) is still insertable and would otherwise hang the page.
MAX_DEPTH = 16

_SUBTREE_SQL = text(
    """
    WITH RECURSIVE subtree(id, depth) AS (
        SELECT id, 0 FROM categories WHERE id = :root
        UNION                       -- UNION, not UNION ALL: dedupes, so a cycle
        SELECT c.id, s.depth + 1    -- terminates instead of looping forever
          FROM categories c
          JOIN subtree s ON c.parent_id = s.id
         WHERE s.depth < :max_depth
    )
    SELECT id FROM subtree
    """
)


def category_subtree(session: Session, root_id: uuid.UUID) -> set[uuid.UUID]:
    """``root_id`` plus every transitive descendant. Cycle-safe."""
    rows = session.execute(
        _SUBTREE_SQL, {"root": root_id, "max_depth": MAX_DEPTH}
    ).scalars()
    return set(rows)


def discretionary_category_ids(session: Session) -> set[uuid.UUID]:
    """Categories whose nature is discretionary."""
    rows = session.execute(
        text("SELECT id FROM categories WHERE nature = :nature"),
        {"nature": CategoryNature.DISCRETIONARY.name},
    ).scalars()
    return set(rows)


def scope_ids(session: Session, category_id: uuid.UUID | None) -> set[uuid.UUID] | None:
    """The category ids a budget counts, or None for a null (discretionary) scope.

    Two rules fall out, and both matter:

    * Uncategorised expense spending counts toward a null-scope budget. ``Category``
      already defaults to discretionary, so excluding NULL would contradict the
      schema's own default and create a budget you evade by simply not tagging.
    * The discretionary filter applies **only** to null scope. An explicitly scoped
      Rent budget counts its whole subtree whatever its nature -- the user named the
      category, so they meant it. Applying the filter globally makes every
      essential-category budget read £0.00 for ever.
    """
    if category_id is None:
        return None
    return category_subtree(session, category_id)


def apply_account_defaults(session: Session, postings: Iterable[Posting]) -> None:
    """Stamp each untagged posting with its account's default category.

    Contractual spending is the case this exists for. Loan interest, bank fees
    and rent paid by standing order arrive with no category, and an uncategorised
    expense leg counts toward a **null-scope** budget by design (see
    :func:`scope_ids`) -- so GBP 50 of interest nobody can avoid consumes 8.3% of a
    GBP 600 discretionary budget, and no amount of discipline moves the number.

    **Stamped on write, never applied on read.** A read-time default would
    re-bucket every historical untagged posting the moment an account's default
    changed: last March's essential rent would silently become discretionary and
    a closed period would stop meaning what it meant. That is the same failure
    archiving-instead-of-deleting exists to prevent.

    Only postings that name no category are touched, and only at creation. An
    explicit ``category_id: null`` on an edit is a decision rather than an
    omission, so editing never re-stamps -- see ``routes.edit_transaction``.
    """
    for p in postings:
        if p.category_id is not None:
            continue
        account = None
        if p.account_id is not None:
            # Identity-map hit for anything already loaded, which is the normal
            # case: the caller has just resolved these accounts to build the legs.
            account = session.get(Account, p.account_id)
        elif p.account is not None:
            account = p.account
        if account is not None and account.default_category_id is not None:
            p.category_id = account.default_category_id
