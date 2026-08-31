"""Statement import endpoints. Plan section 6; Phase 6.

Upload stages; it never posts. Nothing reaches the ledger until a row is
explicitly accepted, and the accept step is what builds the balanced two-leg
transaction — so an import cannot produce an unbalanced one even if the file is
malformed.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import to_minor
from app.db import get_session
from app.domain import importing
from app.models.enums import CandidateStatus
from app.models.imports import ImportBatch, ImportCandidate

router = APIRouter()

#: Statements are text. A 10 MB CSV is about 100,000 rows, far past anything a
#: personal account produces, and refusing early beats parsing a video file.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class CandidateOut(BaseModel):
    id: uuid.UUID
    batch_id: uuid.UUID
    row_number: int
    booking_date: date
    description: str
    merchant: str | None
    amount_minor: int
    status: CandidateStatus
    duplicate_of_transaction_id: uuid.UUID | None
    duplicate_of_candidate_id: uuid.UUID | None
    suggested_category_id: uuid.UUID | None
    transaction_id: uuid.UUID | None
    raw: dict


class BatchOut(BaseModel):
    id: uuid.UUID
    filename: str
    account_id: uuid.UUID
    profile: str
    row_count: int
    #: Counts by status, so the review screen knows what it is opening.
    pending: int
    accepted: int
    rejected: int
    duplicates: int


class AcceptIn(BaseModel):
    #: The other leg. An expense account for spending, an income account for
    #: money in — the same choice manual entry makes.
    counter_account_id: uuid.UUID
    category_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=500)


def _candidate_out(c: ImportCandidate) -> CandidateOut:
    return CandidateOut(
        id=c.id,
        batch_id=c.batch_id,
        row_number=c.row_number,
        booking_date=c.booking_date,
        description=c.description,
        merchant=c.merchant,
        amount_minor=to_minor(c.amount),
        status=c.status,
        duplicate_of_transaction_id=c.duplicate_of_transaction_id,
        duplicate_of_candidate_id=c.duplicate_of_candidate_id,
        suggested_category_id=c.suggested_category_id,
        transaction_id=c.transaction_id,
        raw=c.raw or {},
    )


def _counts(session: Session, batch_id: uuid.UUID) -> dict[CandidateStatus, int]:
    rows = session.execute(
        select(ImportCandidate.status, func.count())
        .where(ImportCandidate.batch_id == batch_id)
        .group_by(ImportCandidate.status)
    ).all()
    return {status: count for status, count in rows}


def _batch_out(session: Session, batch: ImportBatch) -> BatchOut:
    counts = _counts(session, batch.id)
    return BatchOut(
        id=batch.id,
        filename=batch.filename,
        account_id=batch.account_id,
        profile=batch.profile,
        row_count=batch.row_count,
        pending=counts.get(CandidateStatus.PENDING, 0),
        accepted=counts.get(CandidateStatus.ACCEPTED, 0),
        rejected=counts.get(CandidateStatus.REJECTED, 0),
        duplicates=counts.get(CandidateStatus.DUPLICATE, 0),
    )


def _get(session: Session, candidate_id: uuid.UUID) -> ImportCandidate:
    candidate = session.get(ImportCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    return candidate


@router.post("/import", response_model=BatchOut, status_code=201)
async def upload_statement(
    account_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> BatchOut:
    """Parse a statement into candidates. Writes nothing to the ledger."""
    payload = await file.read()
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "That file is larger than 10 MB.")
    try:
        # Bank exports are frequently Windows-encoded and occasionally carry a
        # BOM; utf-8-sig handles both, and latin-1 is the fallback that never
        # raises rather than a guess that might.
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = payload.decode("latin-1")

    try:
        batch = importing.stage(
            session,
            filename=file.filename or "statement.csv",
            content=text,
            account_id=account_id,
        )
    except importing.ImportError_ as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _batch_out(session, batch)


@router.get("/import/batches", response_model=list[BatchOut])
def list_batches(session: Session = Depends(get_session)) -> list[BatchOut]:
    batches = session.scalars(
        select(ImportBatch).order_by(ImportBatch.created_at.desc())
    )
    return [_batch_out(session, b) for b in batches]


@router.get("/import/candidates", response_model=list[CandidateOut])
def list_candidates(
    status: CandidateStatus | None = None,
    batch_id: uuid.UUID | None = None,
    session: Session = Depends(get_session),
) -> list[CandidateOut]:
    """The inbox. Defaults to everything still needing a decision."""
    query = select(ImportCandidate).order_by(
        ImportCandidate.booking_date, ImportCandidate.row_number
    )
    if batch_id is not None:
        query = query.where(ImportCandidate.batch_id == batch_id)
    query = query.where(
        ImportCandidate.status == status
        if status is not None
        else ImportCandidate.status.in_(
            [CandidateStatus.PENDING, CandidateStatus.DUPLICATE]
        )
    )
    return [_candidate_out(c) for c in session.scalars(query)]


@router.post("/import/candidates/{candidate_id}/accept", response_model=CandidateOut)
def accept_candidate(
    candidate_id: uuid.UUID,
    payload: AcceptIn,
    session: Session = Depends(get_session),
) -> CandidateOut:
    candidate = _get(session, candidate_id)
    try:
        importing.accept(
            session,
            candidate,
            counter_account_id=payload.counter_account_id,
            category_id=payload.category_id,
            description=payload.description,
        )
    except importing.ImportError_ as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _candidate_out(candidate)


@router.post("/import/candidates/{candidate_id}/reject", response_model=CandidateOut)
def reject_candidate(
    candidate_id: uuid.UUID, session: Session = Depends(get_session)
) -> CandidateOut:
    try:
        return _candidate_out(importing.reject(session, _get(session, candidate_id)))
    except importing.ImportError_ as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/import/candidates/{candidate_id}/reopen", response_model=CandidateOut)
def reopen_candidate(
    candidate_id: uuid.UUID, session: Session = Depends(get_session)
) -> CandidateOut:
    """Put a rejected or duplicate row back in the queue.

    Duplicate detection is a judgement, not a fact: two identical coffees on the
    same day are a real thing that happens.
    """
    try:
        return _candidate_out(importing.reopen(session, _get(session, candidate_id)))
    except importing.ImportError_ as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
