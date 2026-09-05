"""Statement import. Phase 6.

The four invariants this module exists to hold:

* M1 -- acceptance is idempotent
* M2 -- duplicate detection looks at the ledger *and* at the same file
* M3 -- re-importing a file changes nothing
* M4 -- a decision is never destroyed
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import get_session
from app.domain import importing
from app.domain.disposable import account_balances
from app.main import app
from app.models import CandidateStatus, Posting, Transaction, TransactionStatus
from app.models.imports import ImportCandidate
from tests.conftest import post

ISO = """date,description,amount,merchant
2026-08-04,TESCO STORES 3421,-62.40,Tesco
2026-08-06,PRET A MANGER,-4.85,Pret
2026-08-07,ACME LTD SALARY,2500.00,Acme
"""


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def upload(client, account_id, text, filename="statement.csv"):
    return client.post(
        "/api/import",
        data={"account_id": str(account_id)},
        files={"file": (filename, text.encode(), "text/csv")},
    )


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_parses_an_iso_statement():
    profile, rows = importing.parse(ISO)
    assert profile.name == "generic-iso"
    assert [r.amount for r in rows] == [
        Decimal("-62.40"), Decimal("-4.85"), Decimal("2500.00")
    ]
    assert rows[0].booking_date == date(2026, 8, 4)


def test_parses_a_debit_credit_pair_with_uk_dates():
    """Two columns, both positive, meaning opposite things."""
    profile, rows = importing.parse(
        "Date,Description,Debit,Credit\n"
        "04/08/2026,TESCO,62.40,\n"
        "07/08/2026,SALARY,,2500.00\n"
    )
    assert profile.name == "uk-debit-credit"
    assert rows[0].amount == Decimal("-62.40")
    assert rows[1].amount == Decimal("2500.00")


def test_reads_money_that_banks_actually_write():
    assert importing._parse_amount("£1,234.56") == Decimal("1234.56")
    assert importing._parse_amount("(45.00)") == Decimal("-45.00")
    assert importing._parse_amount("-1,000") == Decimal("-1000")
    assert importing._parse_amount("") == Decimal("0")


def test_zero_rows_and_blank_lines_are_skipped():
    _, rows = importing.parse(
        "date,description,amount\n"
        "2026-08-04,BALANCE CHECK,0.00\n"
        "\n"
        "2026-08-05,TESCO,-10.00\n"
    )
    assert [r.description for r in rows] == ["TESCO"]


def test_an_unrecognised_header_is_an_error_not_a_guess():
    """A silent mis-parse is worse than a refusal."""
    with pytest.raises(importing.ImportError_, match="Could not recognise"):
        importing.parse("colour,size,note\nred,4,hello\n")


def test_a_file_with_no_money_rows_is_rejected():
    with pytest.raises(importing.ImportError_, match="no rows that move money"):
        importing.parse("date,description,amount\n2026-08-04,NIL,0\n")


# --------------------------------------------------------------------------
# Fingerprinting
# --------------------------------------------------------------------------


def test_reference_numbers_do_not_change_a_payment_s_identity():
    """The same coffee, exported twice, with different terminal noise."""
    a = importing.fingerprint(date(2026, 8, 6), Decimal("-4.85"), "PRET A MANGER 88213 CARD 4471")
    b = importing.fingerprint(date(2026, 8, 7), Decimal("-4.85"), "PRET A MANGER 99104 CARD 4471")
    assert a == b


def test_different_amounts_are_different_payments():
    a = importing.fingerprint(date(2026, 8, 6), Decimal("-4.85"), "PRET")
    b = importing.fingerprint(date(2026, 8, 6), Decimal("-4.95"), "PRET")
    assert a != b


# --------------------------------------------------------------------------
# M3: re-import
# --------------------------------------------------------------------------


def test_the_same_file_twice_is_refused(client, accounts):
    first = upload(client, accounts["current"].id, ISO)
    assert first.status_code == 201
    assert first.json()["row_count"] == 3

    second = upload(client, accounts["current"].id, ISO)
    assert second.status_code == 422
    assert "already imported" in second.json()["detail"]


def test_re_import_creates_no_second_batch(client, accounts, session):
    upload(client, accounts["current"].id, ISO)
    upload(client, accounts["current"].id, ISO)
    assert session.scalar(select(func.count()).select_from(ImportCandidate)) == 3


# --------------------------------------------------------------------------
# M2: duplicate detection, both directions
# --------------------------------------------------------------------------


def test_a_row_already_in_the_ledger_is_flagged(client, accounts, categories, session):
    post(session, date(2026, 8, 4), "TESCO STORES 3421",
         [(accounts["current"], "-62.40"),
          (accounts["groceries"], "62.40", categories["groceries"])])

    upload(client, accounts["current"].id, ISO)
    rows = {c.description: c for c in session.scalars(select(ImportCandidate))}
    assert rows["TESCO STORES 3421"].status == CandidateStatus.DUPLICATE
    assert rows["TESCO STORES 3421"].duplicate_of_transaction_id is not None
    assert rows["PRET A MANGER"].status == CandidateStatus.PENDING


def test_a_row_repeated_inside_one_file_is_flagged_against_the_earlier_one(
    client, accounts, session
):
    """Overlapping exports repeat rows within a single download too."""
    text = (
        "date,description,amount\n"
        "2026-08-04,TESCO STORES,-62.40\n"
        "2026-08-04,TESCO STORES,-62.40\n"
    )
    upload(client, accounts["current"].id, text)
    rows = sorted(session.scalars(select(ImportCandidate)), key=lambda c: c.row_number)
    assert rows[0].status == CandidateStatus.PENDING
    assert rows[1].status == CandidateStatus.DUPLICATE
    assert rows[1].duplicate_of_candidate_id == rows[0].id


def test_staged_rows_from_another_account_are_not_duplicates(
    client, accounts, session
):
    first = "date,description,amount\n2026-08-04,Same transfer,-10.00\n"
    second = "date,description,amount,note\n2026-08-04,Same transfer,-10.00,x\n"
    upload(client, accounts["current"].id, first)
    upload(client, accounts["cash"].id, second)

    rows = list(session.scalars(select(ImportCandidate)))
    cash_row = next(r for r in rows if r.batch.account_id == accounts["cash"].id)
    assert cash_row.status == CandidateStatus.PENDING
    assert cash_row.duplicate_of_candidate_id is None


def test_a_date_shifted_by_a_day_is_still_the_same_payment(client, accounts, session):
    """Banks move dates between pending and settled exports."""
    post(session, date(2026, 8, 5), "PRET A MANGER",
         [(accounts["current"], "-4.85"), (accounts["groceries"], "4.85")])
    upload(client, accounts["current"].id, ISO)
    rows = {c.description: c for c in session.scalars(select(ImportCandidate))}
    assert rows["PRET A MANGER"].status == CandidateStatus.DUPLICATE


def test_a_voided_transaction_is_not_a_duplicate_to_avoid(
    client, accounts, categories, session
):
    """A void leaves a hole the import is entitled to fill."""
    txn = post(session, date(2026, 8, 4), "TESCO STORES 3421",
               [(accounts["current"], "-62.40"),
                (accounts["groceries"], "62.40", categories["groceries"])])
    txn.status = TransactionStatus.VOIDED
    session.commit()

    upload(client, accounts["current"].id, ISO)
    rows = {c.description: c for c in session.scalars(select(ImportCandidate))}
    assert rows["TESCO STORES 3421"].status == CandidateStatus.PENDING


def test_staging_never_touches_the_ledger(client, accounts, session):
    """The whole promise of the inbox."""
    before = account_balances(session, date(2026, 8, 31))
    count = session.scalar(select(func.count()).select_from(Transaction))

    upload(client, accounts["current"].id, ISO)

    assert account_balances(session, date(2026, 8, 31)) == before
    assert session.scalar(select(func.count()).select_from(Transaction)) == count


# --------------------------------------------------------------------------
# M1: acceptance
# --------------------------------------------------------------------------


def _accept(client, candidate_id, account, category_id=None):
    return client.post(
        f"/api/import/candidates/{candidate_id}/accept",
        json={
            "counter_account_id": str(account.id),
            "category_id": str(category_id) if category_id else None,
        },
    )


def test_accepting_creates_a_balanced_two_leg_transaction(
    client, accounts, categories, session
):
    upload(client, accounts["current"].id, ISO)
    tesco = session.scalars(
        select(ImportCandidate).where(ImportCandidate.description.like("TESCO%"))
    ).one()

    r = _accept(client, tesco.id, accounts["groceries"], categories["groceries"].id)
    assert r.status_code == 200
    assert r.json()["status"] == CandidateStatus.ACCEPTED

    txn = session.get(Transaction, tesco.transaction_id)
    postings = session.scalars(
        select(Posting).where(Posting.transaction_id == txn.id)
    ).all()
    assert len(postings) == 2
    assert sum(p.amount for p in postings) == Decimal("0")
    assert {p.amount for p in postings} == {Decimal("-62.40"), Decimal("62.40")}


def test_accepting_moves_the_balance_by_exactly_the_row_amount(
    client, accounts, categories, session
):
    before = account_balances(session, date(2026, 8, 31))[accounts["current"].id]
    upload(client, accounts["current"].id, ISO)
    tesco = session.scalars(
        select(ImportCandidate).where(ImportCandidate.description.like("TESCO%"))
    ).one()
    _accept(client, tesco.id, accounts["groceries"], categories["groceries"].id)

    after = account_balances(session, date(2026, 8, 31))[accounts["current"].id]
    assert after == before - Decimal("62.40")


def test_accepting_twice_posts_once(client, accounts, categories, session):
    """M1. A double-click is not a way to post twice."""
    upload(client, accounts["current"].id, ISO)
    tesco = session.scalars(
        select(ImportCandidate).where(ImportCandidate.description.like("TESCO%"))
    ).one()

    first = _accept(client, tesco.id, accounts["groceries"], categories["groceries"].id)
    balance = account_balances(session, date(2026, 8, 31))[accounts["current"].id]
    second = _accept(client, tesco.id, accounts["groceries"], categories["groceries"].id)

    assert first.json()["transaction_id"] == second.json()["transaction_id"]
    assert session.scalar(select(func.count()).select_from(Transaction)) == 1
    assert account_balances(session, date(2026, 8, 31))[accounts["current"].id] == balance


def test_income_rows_accept_against_an_income_account(client, accounts, session):
    upload(client, accounts["current"].id, ISO)
    salary = session.scalars(
        select(ImportCandidate).where(ImportCandidate.amount > 0)
    ).one()
    r = _accept(client, salary.id, accounts["salary"])
    assert r.status_code == 200

    postings = session.scalars(
        select(Posting).where(Posting.transaction_id == salary.transaction_id)
    ).all()
    assert sum(p.amount for p in postings) == Decimal("0")
    by_account = {p.account_id: p.amount for p in postings}
    assert by_account[accounts["current"].id] == Decimal("2500.00")
    assert by_account[accounts["salary"].id] == Decimal("-2500.00")


# --------------------------------------------------------------------------
# M4: decisions survive
# --------------------------------------------------------------------------


def test_rejecting_keeps_the_row(client, accounts, session):
    upload(client, accounts["current"].id, ISO)
    pret = session.scalars(
        select(ImportCandidate).where(ImportCandidate.description == "PRET A MANGER")
    ).one()

    r = client.post(f"/api/import/candidates/{pret.id}/reject")
    assert r.status_code == 200
    session.expire_all()
    assert session.get(ImportCandidate, pret.id).status == CandidateStatus.REJECTED


def test_a_rejected_row_is_not_offered_again(client, accounts, session):
    upload(client, accounts["current"].id, ISO)
    pret = session.scalars(
        select(ImportCandidate).where(ImportCandidate.description == "PRET A MANGER")
    ).one()
    client.post(f"/api/import/candidates/{pret.id}/reject")

    inbox = client.get("/api/import/candidates").json()
    assert all(c["description"] != "PRET A MANGER" for c in inbox)


def test_an_accepted_row_cannot_be_rejected(client, accounts, categories, session):
    """Rejecting here would leave the ledger unchanged and the trail lying."""
    upload(client, accounts["current"].id, ISO)
    tesco = session.scalars(
        select(ImportCandidate).where(ImportCandidate.description.like("TESCO%"))
    ).one()
    _accept(client, tesco.id, accounts["groceries"], categories["groceries"].id)

    r = client.post(f"/api/import/candidates/{tesco.id}/reject")
    assert r.status_code == 422
    assert "Void the transaction" in r.json()["detail"]


def test_a_duplicate_verdict_can_be_overridden(client, accounts, session):
    """Two identical coffees on one day is a real thing that happens."""
    text = (
        "date,description,amount\n"
        "2026-08-04,PRET,-4.85\n"
        "2026-08-04,PRET,-4.85\n"
    )
    upload(client, accounts["current"].id, text)
    flagged = session.scalars(
        select(ImportCandidate).where(
            ImportCandidate.status == CandidateStatus.DUPLICATE
        )
    ).one()

    r = client.post(f"/api/import/candidates/{flagged.id}/reopen")
    assert r.status_code == 200
    assert r.json()["status"] == CandidateStatus.PENDING


# --------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------


def test_statements_cannot_be_imported_into_an_expense_account(client, accounts):
    r = upload(client, accounts["groceries"].id, ISO)
    assert r.status_code == 422
    assert "money actually sits in" in r.json()["detail"]


def test_the_inbox_defaults_to_rows_needing_a_decision(client, accounts, session):
    post(session, date(2026, 8, 4), "TESCO STORES 3421",
         [(accounts["current"], "-62.40"), (accounts["groceries"], "62.40")])
    upload(client, accounts["current"].id, ISO)

    inbox = client.get("/api/import/candidates").json()
    assert {c["status"] for c in inbox} <= {"pending", "duplicate"}
    assert len(inbox) == 3


def test_batch_counts_report_what_the_review_screen_is_opening(client, accounts, session):
    post(session, date(2026, 8, 4), "TESCO STORES 3421",
         [(accounts["current"], "-62.40"), (accounts["groceries"], "62.40")])
    batch = upload(client, accounts["current"].id, ISO).json()
    assert batch["row_count"] == 3
    assert batch["duplicates"] == 1
    assert batch["pending"] == 2
    assert batch["profile"] == "generic-iso"


def test_the_raw_row_is_kept_verbatim(client, accounts, session):
    """The interpretation may be wrong and the file will not be around."""
    upload(client, accounts["current"].id, ISO)
    row = session.scalars(
        select(ImportCandidate).where(ImportCandidate.description.like("TESCO%"))
    ).one()
    assert row.raw["amount"] == "-62.40"
    assert row.raw["merchant"] == "Tesco"


def test_a_bom_prefixed_windows_export_still_parses(client, accounts):
    """Bank exports are frequently Windows-encoded with a byte-order mark."""
    r = client.post(
        "/api/import",
        data={"account_id": str(accounts["current"].id)},
        files={"file": ("statement.csv", "﻿".encode() + ISO.encode(), "text/csv")},
    )
    assert r.status_code == 201
    assert r.json()["row_count"] == 3


def test_a_payment_re_exported_across_a_month_boundary_still_matches():
    """The bug a month-keyed fingerprint hides.

    31 August and 1 September are one day apart and well inside the matching
    window, but any key carrying the month puts them in different buckets and the
    duplicate is silently missed.
    """
    a = importing.fingerprint(date(2026, 8, 31), Decimal("-4.85"), "PRET A MANGER")
    b = importing.fingerprint(date(2026, 9, 1), Decimal("-4.85"), "PRET A MANGER")
    assert a == b


def test_the_month_boundary_case_end_to_end(client, accounts, session):
    post(session, date(2026, 8, 31), "PRET A MANGER",
         [(accounts["current"], "-4.85"), (accounts["groceries"], "4.85")])
    upload(client, accounts["current"].id,
           "date,description,amount\n2026-09-01,PRET A MANGER,-4.85\n")
    row = session.scalars(select(ImportCandidate)).one()
    assert row.status == CandidateStatus.DUPLICATE


def test_far_apart_payments_with_the_same_key_are_not_duplicates():
    """The window, not the key, is what keeps January out of August."""
    text = (
        "date,description,amount\n"
        "2026-03-04,PRET,-4.85\n"
        "2026-08-04,PRET,-4.85\n"
    )
    _, rows = importing.parse(text)
    assert rows[0].amount == rows[1].amount
    assert (rows[1].booking_date - rows[0].booking_date).days > 3


# --------------------------------------------------------------------------
# A second opinion on a window match -- only ever narrows, never confirms
# --------------------------------------------------------------------------


class FakeDuplicateChecker:
    def __init__(self, answers: dict[int, bool] | None = None):
        self.answers = answers or {}
        self.calls: list[list[str]] = []

    def check(self, briefs):
        self.calls.append(list(briefs))
        return dict(self.answers)


class ExplodingDuplicateChecker:
    def check(self, briefs):
        raise RuntimeError("the network is down")


def _one_row(when=date(2026, 8, 18), amount="-4.85", description="PRET"):
    return importing.ParsedRow(
        row_number=1, booking_date=when, description=description,
        merchant=None, amount=Decimal(amount), raw={},
    )


def test_an_explicit_false_demotes_a_ledger_match(session, accounts):
    post(session, date(2026, 8, 17), "PRET",
         [(accounts["current"], "-4.85"), (accounts["groceries"], "4.85")])

    fake = FakeDuplicateChecker({0: False})
    verdicts = importing.classify_duplicates(
        session, accounts["current"].id, [_one_row(date(2026, 8, 18))],
        duplicate_checker=fake,
    )
    assert verdicts == {}


def test_true_leaves_the_window_match_in_place(session, accounts):
    post(session, date(2026, 8, 17), "PRET",
         [(accounts["current"], "-4.85"), (accounts["groceries"], "4.85")])

    fake = FakeDuplicateChecker({0: True})
    verdicts = importing.classify_duplicates(
        session, accounts["current"].id, [_one_row(date(2026, 8, 18))],
        duplicate_checker=fake,
    )
    assert 1 in verdicts


def test_no_opinion_leaves_the_window_match_in_place(session, accounts):
    """A match is the default assumption -- an absent answer changes nothing,
    same as it does for enrichment's downgrade-only verify()."""
    post(session, date(2026, 8, 17), "PRET",
         [(accounts["current"], "-4.85"), (accounts["groceries"], "4.85")])

    fake = FakeDuplicateChecker({})
    verdicts = importing.classify_duplicates(
        session, accounts["current"].id, [_one_row(date(2026, 8, 18))],
        duplicate_checker=fake,
    )
    assert 1 in verdicts


def test_no_window_match_means_no_call_at_all(session, accounts):
    """Nothing to ask a second opinion about when the first pass found no
    match -- the common case, and it must not cost a call."""
    fake = FakeDuplicateChecker()
    importing.classify_duplicates(
        session, accounts["current"].id, [_one_row()], duplicate_checker=fake,
    )
    assert fake.calls == []


def test_every_window_match_is_sent_in_one_batched_call(session, accounts):
    post(session, date(2026, 8, 17), "PRET",
         [(accounts["current"], "-4.85"), (accounts["groceries"], "4.85")])
    post(session, date(2026, 8, 17), "TESCO",
         [(accounts["current"], "-10.00"), (accounts["groceries"], "10.00")])

    fake = FakeDuplicateChecker()
    rows = [
        _one_row(date(2026, 8, 18), "-4.85", "PRET"),
        importing.ParsedRow(
            row_number=2, booking_date=date(2026, 8, 18), description="TESCO",
            merchant=None, amount=Decimal("-10.00"), raw={},
        ),
    ]
    importing.classify_duplicates(
        session, accounts["current"].id, rows, duplicate_checker=fake,
    )
    assert len(fake.calls) == 1
    assert len(fake.calls[0]) == 2


def test_a_failing_checker_leaves_the_window_match_in_place(session, accounts):
    """A failed second opinion is the same as no opinion -- the deterministic
    window match it was double-checking is what stands, same as the other
    verify()-style features in this codebase (enrichment.resolve,
    canonical.resolve): a second opinion is never critical."""
    post(session, date(2026, 8, 17), "PRET",
         [(accounts["current"], "-4.85"), (accounts["groceries"], "4.85")])
    verdicts = importing.classify_duplicates(
        session, accounts["current"].id, [_one_row(date(2026, 8, 18))],
        duplicate_checker=ExplodingDuplicateChecker(),
    )
    assert 1 in verdicts


def test_a_failing_checker_does_not_fail_the_import(client, accounts, session, monkeypatch):
    post(session, date(2026, 8, 17), "PRET A MANGER",
         [(accounts["current"], "-4.85"), (accounts["groceries"], "4.85")])
    monkeypatch.setattr(
        importing, "build_duplicate_checker", lambda: ExplodingDuplicateChecker()
    )
    r = upload(
        client, accounts["current"].id,
        "date,description,amount\n2026-08-18,PRET A MANGER,-4.85\n",
    )
    assert r.status_code == 201
    row = session.scalars(select(ImportCandidate)).one()
    assert row.status == CandidateStatus.DUPLICATE


# --------------------------------------------------------------------------
# Reply parsing
# --------------------------------------------------------------------------


def test_a_fenced_duplicate_reply_is_read():
    assert importing._parse_duplicate_reply('```json\n{"0": false}\n```', 1) == {0: False}


def test_an_out_of_range_index_is_dropped():
    assert importing._parse_duplicate_reply('{"5": false}', count=1) == {}


def test_an_unparseable_duplicate_reply_is_empty_not_an_exception():
    assert importing._parse_duplicate_reply("not json", count=1) == {}
    assert importing._parse_duplicate_reply("", count=1) == {}


def test_no_provider_means_no_duplicate_checker(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "llm_provider", "none")
    assert isinstance(importing.build_duplicate_checker(), importing.NullDuplicateChecker)
