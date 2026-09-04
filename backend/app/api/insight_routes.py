"""Explanations and insights. Plan section 11; Phase 9.

Read-only by construction: there is no write verb in this file. Invariant E3
says an insight never acts, and the cheapest way to guarantee that is to give it
nowhere to act from.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.schemas import to_minor
from app.db import get_session
from app.domain import explain, insights, narrate

router = APIRouter()


class TermOut(BaseModel):
    label: str
    #: Signed as it contributes, so the client adds rather than deciding.
    amount_minor: int
    detail: str
    parts: list["TermOut"] = []


class DerivationOut(BaseModel):
    figure: str
    total_minor: int
    note: str
    terms: list[TermOut]


class EvidenceOut(BaseModel):
    label: str
    amount_minor: int | None
    detail: str


class InsightOut(BaseModel):
    kind: str
    severity: str
    title: str
    detail: str
    action: str
    evidence: list[EvidenceOut]
    #: What this insight is about, when it's about one specific thing --
    #: lets the client link straight to the transactions behind it.
    subject_merchant: str | None
    subject_category_id: uuid.UUID | None
    #: A friendlier rewrite of `detail`, built only from the evidence already
    #: attached (see domain/narrate.py). None with no provider, or if the
    #: model produced nothing usable -- `detail` is always a complete
    #: sentence on its own and is what the client falls back to.
    narration: str | None


def _term(t: explain.Term) -> TermOut:
    return TermOut(
        label=t.label,
        amount_minor=to_minor(t.amount),
        detail=t.detail,
        parts=[_term(p) for p in t.parts],
    )


def _derivation(d: explain.Derivation) -> DerivationOut:
    return DerivationOut(
        figure=d.figure,
        total_minor=to_minor(d.total),
        note=d.note,
        terms=[_term(t) for t in d.terms],
    )


@router.get("/explain/safe-to-spend", response_model=DerivationOut)
def explain_safe_to_spend(
    on: date | None = None, session: Session = Depends(get_session)
) -> DerivationOut:
    return _derivation(explain.safe_to_spend(session, on))


@router.get("/explain/total-accessible", response_model=DerivationOut)
def explain_total_accessible(
    on: date | None = None, session: Session = Depends(get_session)
) -> DerivationOut:
    return _derivation(explain.total_accessible(session, on))


@router.get("/explain/net-worth", response_model=DerivationOut)
def explain_net_worth(
    on: date | None = None, session: Session = Depends(get_session)
) -> DerivationOut:
    return _derivation(explain.net_worth_breakdown(session, on))


@router.get("/explain/budget/{budget_id}", response_model=DerivationOut)
def explain_budget(
    budget_id: uuid.UUID,
    on: date | None = None,
    session: Session = Depends(get_session),
) -> DerivationOut:
    result = explain.budget_period(session, budget_id, on)
    if result is None:
        raise HTTPException(404, "no current period for that budget")
    return _derivation(result)


@router.get("/insights", response_model=list[InsightOut])
def list_insights(
    on: date | None = None, session: Session = Depends(get_session)
) -> list[InsightOut]:
    """Everything worth mentioning, worst first."""
    found = insights.collect(session, on)

    # One batched call for the whole page rather than one per insight -- see
    # domain/narrate.py for why this is the one LLM feature with no cache.
    try:
        narrations = narrate.narrate_all(found)
    except Exception as exc:  # noqa: BLE001 -- a narration is never critical
        import logging

        logging.getLogger("uvicorn.error").warning("narration skipped: %s", exc)
        narrations = {}

    return [
        InsightOut(
            kind=i.kind,
            severity=i.severity,
            title=i.title,
            detail=i.detail,
            action=i.action,
            evidence=[
                EvidenceOut(
                    label=e.label,
                    amount_minor=to_minor(e.amount) if e.amount is not None else None,
                    detail=e.detail,
                )
                for e in i.evidence
            ],
            subject_merchant=i.subject_merchant,
            subject_category_id=i.subject_category_id,
            narration=narrations.get(index),
        )
        for index, i in enumerate(found)
    ]
