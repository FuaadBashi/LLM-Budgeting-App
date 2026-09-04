"""Receipt reading. Phase 7.

A1' -- no model output reaches the ledger without a person confirming it. A
receipt becomes a candidate, exactly like a bank row, and posts nothing until
someone accepts it.

No test here touches the network. The reader is injected.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import get_session
from app.domain import receipts
from app.domain.disposable import account_balances
from app.main import app
from app.models import CandidateStatus, Transaction
from app.models.imports import ImportBatch, ImportCandidate
from tests.conftest import post

TODAY = date(2026, 8, 20)
JPEG = b"\xff\xd8\xff\xe0" + b"fake image bytes"


class FakeReader:
    model = "fake-vision"

    def __init__(
        self,
        read: receipts.ReceiptRead,
        verification: receipts.ReceiptVerification | None = None,
    ):
        self._read = read
        self._verification = verification
        self.calls = 0
        self.verify_calls = 0

    def read(self, image, media_type):
        self.calls += 1
        return self._read

    def verify(self, image, media_type, read):
        self.verify_calls += 1
        return self._verification


class ReaderWithNoVerify:
    """Mimics a reader written before verify() existed -- stage() must not
    crash just because the second opinion isn't available."""

    model = "old-style"

    def __init__(self, read: receipts.ReceiptRead):
        self._read = read

    def read(self, image, media_type):
        return self._read


def a_receipt(
    total="42.30",
    merchant="DISHOOM",
    when=date(2026, 8, 18),
    confident=True,
    line_items=(),
):
    return receipts.ReceiptRead(
        merchant=merchant,
        when=when,
        total=Decimal(total) if total is not None else None,
        confident=confident,
        line_items=line_items,
    )


def item(description, amount):
    return receipts.LineItem(description=description, amount=Decimal(amount))


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def stage(session, accounts, reader, image=JPEG, media_type="image/jpeg"):
    return receipts.stage(
        session,
        filename="receipt.jpg",
        image=image,
        media_type=media_type,
        account_id=accounts["current"].id,
        today=TODAY,
        reader=reader,
    )


# --------------------------------------------------------------------------
# A1'
# --------------------------------------------------------------------------


def test_a_receipt_becomes_a_candidate_not_a_transaction(session, accounts):
    """A1'. The model proposed a number; nothing was recorded."""
    before = session.scalar(select(func.count()).select_from(Transaction))
    balances = account_balances(session, TODAY)

    candidate = stage(session, accounts, FakeReader(a_receipt()))

    assert candidate.status == CandidateStatus.PENDING
    assert candidate.transaction_id is None
    assert session.scalar(select(func.count()).select_from(Transaction)) == before
    assert account_balances(session, TODAY) == balances


def test_accepting_a_receipt_posts_it_like_any_other_candidate(
    session, accounts, categories
):
    """One path to the ledger, not two."""
    from app.domain import importing

    candidate = stage(session, accounts, FakeReader(a_receipt()))
    before = account_balances(session, TODAY)[accounts["current"].id]

    importing.accept(
        session, candidate,
        counter_account_id=accounts["groceries"].id,
        category_id=categories["restaurants"].id,
    )

    after = account_balances(session, TODAY)[accounts["current"].id]
    assert after == before - Decimal("42.30")


def test_a_receipt_is_money_out(session, accounts):
    """A receipt is evidence of a payment, never of income."""
    candidate = stage(session, accounts, FakeReader(a_receipt(total="42.30")))
    assert candidate.amount == Decimal("-42.30")


def test_a_positive_total_is_still_recorded_as_spending(session, accounts):
    candidate = stage(session, accounts, FakeReader(a_receipt(total="9.99")))
    assert candidate.amount < 0


# --------------------------------------------------------------------------
# Refusing rather than guessing
# --------------------------------------------------------------------------


def test_an_unreadable_receipt_is_refused_not_guessed(session, accounts):
    """A plausible wrong total is the one that gets waved through."""
    reader = FakeReader(a_receipt(total=None, merchant=None, confident=False))
    with pytest.raises(receipts.ReceiptError, match="Could not read a total"):
        stage(session, accounts, reader)


def test_a_refused_receipt_leaves_nothing_behind(session, accounts):
    reader = FakeReader(a_receipt(total=None, confident=False))
    with pytest.raises(receipts.ReceiptError):
        stage(session, accounts, reader)
    assert session.scalar(select(func.count()).select_from(ImportBatch)) == 0
    assert session.scalar(select(func.count()).select_from(ImportCandidate)) == 0


def test_a_zero_total_is_not_usable(session, accounts):
    with pytest.raises(receipts.ReceiptError):
        stage(session, accounts, FakeReader(a_receipt(total="0")))


def test_an_unconfident_read_is_staged_but_says_so(session, accounts):
    """Low confidence still goes to review -- with the doubt recorded."""
    candidate = stage(session, accounts, FakeReader(a_receipt(confident=False)))
    batch = session.get(ImportBatch, candidate.batch_id)
    assert "not confident" in batch.notes
    assert candidate.raw["confident"] is False


def test_a_missing_date_falls_back_to_today(session, accounts):
    candidate = stage(session, accounts, FakeReader(a_receipt(when=None)))
    assert candidate.booking_date == TODAY


def test_a_missing_merchant_still_stages(session, accounts):
    candidate = stage(session, accounts, FakeReader(a_receipt(merchant=None)))
    assert candidate.description == "Receipt"


# --------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------


def test_a_non_image_is_refused(session, accounts):
    with pytest.raises(receipts.ReceiptError, match="not an image"):
        stage(session, accounts, FakeReader(a_receipt()), media_type="application/pdf")


def test_an_oversized_image_is_refused_before_it_is_read(session, accounts):
    reader = FakeReader(a_receipt())
    with pytest.raises(receipts.ReceiptError, match="larger than 8 MB"):
        stage(session, accounts, reader, image=b"x" * (receipts.MAX_IMAGE_BYTES + 1))
    assert reader.calls == 0, "an oversized image must not reach the model"


def test_receipts_cannot_be_recorded_against_an_expense_account(session, accounts):
    with pytest.raises(receipts.ReceiptError, match="money actually left"):
        receipts.stage(
            session, filename="r.jpg", image=JPEG, media_type="image/jpeg",
            account_id=accounts["groceries"].id, today=TODAY,
            reader=FakeReader(a_receipt()),
        )


def test_the_same_photograph_twice_is_refused(session, accounts):
    """M3, reused. Photographing a receipt again is a normal thing to do."""
    stage(session, accounts, FakeReader(a_receipt()))
    with pytest.raises(receipts.ReceiptError, match="already been read"):
        stage(session, accounts, FakeReader(a_receipt()))


def test_a_different_photograph_of_a_similar_purchase_is_allowed(session, accounts):
    """Different bytes, so a genuinely separate upload gets through."""
    stage(session, accounts, FakeReader(a_receipt()))
    second = stage(
        session, accounts, FakeReader(a_receipt(total="8.20", merchant="PRET")),
        image=JPEG + b"different",
    )
    assert second.id is not None


# --------------------------------------------------------------------------
# Reusing Phase 6's plumbing
# --------------------------------------------------------------------------


def test_a_receipt_for_an_already_imported_payment_is_flagged(
    session, accounts, categories
):
    """The common case: the bank row arrived first."""
    post(session, date(2026, 8, 18), "DISHOOM",
         [(accounts["current"], "-42.30"),
          (accounts["groceries"], "42.30", categories["restaurants"])])

    candidate = stage(session, accounts, FakeReader(a_receipt()))
    assert candidate.status == CandidateStatus.DUPLICATE
    assert candidate.duplicate_of_transaction_id is not None


def test_the_raw_read_is_kept_verbatim(session, accounts):
    """The photograph is not stored, so the reading of it must be."""
    candidate = stage(session, accounts, FakeReader(a_receipt()))
    assert candidate.raw["total"] == "42.30"
    assert candidate.raw["merchant"] == "DISHOOM"
    assert candidate.raw["source"] == "receipt"


# --------------------------------------------------------------------------
# The second opinion -- informational only, A1' unmoved
# --------------------------------------------------------------------------


def test_a_second_check_that_disagrees_is_surfaced_not_applied(session, accounts):
    """The whole point: a disagreeing second pass is a note, not a correction."""
    reader = FakeReader(
        a_receipt(total="42.30"),
        receipts.ReceiptVerification(matches=False, note="total looks like 45.30"),
    )
    candidate = stage(session, accounts, reader)
    batch = session.get(ImportBatch, candidate.batch_id)

    assert "Second check: total looks like 45.30" in batch.notes
    assert candidate.raw["verification_matches"] is False
    assert candidate.raw["verification_note"] == "total looks like 45.30"
    # Not applied: the staged amount is still the first read's, untouched.
    assert candidate.amount == Decimal("-42.30")


def test_a_second_check_that_agrees_adds_no_note(session, accounts):
    reader = FakeReader(
        a_receipt(), receipts.ReceiptVerification(matches=True, note="")
    )
    candidate = stage(session, accounts, reader)
    batch = session.get(ImportBatch, candidate.batch_id)

    assert "Second check" not in batch.notes
    assert candidate.raw["verification_matches"] is True


def test_no_second_opinion_is_not_treated_as_a_disagreement(session, accounts):
    """None means no opinion was formed -- never surfaced as a false match."""
    candidate = stage(session, accounts, FakeReader(a_receipt(), verification=None))
    batch = session.get(ImportBatch, candidate.batch_id)

    assert "Second check" not in batch.notes
    assert candidate.raw["verification_matches"] is None
    assert candidate.raw["verification_note"] == ""


def test_a_reader_without_verify_does_not_crash_staging(session, accounts):
    """Graceful degradation for anything that predates the second pass."""
    candidate = stage(session, accounts, ReaderWithNoVerify(a_receipt()))
    assert candidate.id is not None


class _FakeCategorySuggester:
    """A minimal enrichment.Suggester for exercising the category-verification
    path from inside a receipt upload, without touching test_enrichment.py."""

    model = "fake"

    def __init__(self, answers, verdicts):
        self.answers = answers
        self.verdicts = verdicts

    def suggest(self, descriptions, categories):
        return {d: self.answers.get(d) for d in descriptions if d in self.answers}

    def verify(self, picks):
        return {d: v for d, v in self.verdicts.items() if d in picks}


def test_a_flagged_category_guess_appends_to_the_verification_note(
    session, accounts, categories, monkeypatch
):
    """A disagreement on the receipt's suggested category is surfaced the
    same way a disagreement on the amount is -- appended, not overwritten,
    since the image read may have already left its own note here."""
    from app.domain import enrichment

    fake = _FakeCategorySuggester(
        {"DISHOOM": "Restaurants"}, verdicts={"DISHOOM": False}
    )
    monkeypatch.setattr(enrichment, "build_suggester", lambda: fake)

    reader = FakeReader(
        a_receipt(merchant="DISHOOM"),
        receipts.ReceiptVerification(matches=False, note="total looks off"),
    )
    candidate = stage(session, accounts, reader)

    note = candidate.raw["verification_note"]
    assert "total looks off" in note
    assert "didn't agree" in note
    assert candidate.suggested_category_id is None


def test_a_failing_verification_call_does_not_fail_the_upload(session, accounts):
    class ExplodingVerifyReader(FakeReader):
        def verify(self, image, media_type, read):
            raise RuntimeError("the network is down")

    candidate = stage(session, accounts, ExplodingVerifyReader(a_receipt()))
    assert candidate.id is not None
    assert candidate.raw["verification_matches"] is None


# --------------------------------------------------------------------------
# Line-item splitting -- only ever when the lines sum to the total
# --------------------------------------------------------------------------


def test_line_items_that_sum_to_the_total_become_separate_candidates(session, accounts):
    read = a_receipt(
        total="12.00",
        line_items=(item("Milk", "2.00"), item("Bread", "10.00")),
    )
    stage(session, accounts, FakeReader(read))

    rows = session.scalars(select(ImportCandidate)).all()
    assert sorted(r.description for r in rows) == ["Bread", "Milk"]
    assert sorted(r.amount for r in rows) == [Decimal("-10.00"), Decimal("-2.00")]


def test_the_batch_row_count_matches_the_split(session, accounts):
    read = a_receipt(
        total="12.00",
        line_items=(item("Milk", "2.00"), item("Bread", "10.00")),
    )
    candidate = stage(session, accounts, FakeReader(read))
    batch = session.get(ImportBatch, candidate.batch_id)
    assert batch.row_count == 2


def test_a_split_candidate_notes_the_receipt_total_it_came_from(session, accounts):
    read = a_receipt(
        total="12.00",
        line_items=(item("Milk", "2.00"), item("Bread", "10.00")),
    )
    stage(session, accounts, FakeReader(read))
    rows = session.scalars(select(ImportCandidate)).all()
    assert all(r.raw["split_of_total"] == "12.00" for r in rows)


def test_line_items_that_do_not_sum_to_the_total_stay_one_candidate(session, accounts):
    """The reader offered a split, but the arithmetic doesn't check out --
    one candidate for the whole receipt is the safe fallback, not three
    guessed line amounts."""
    read = a_receipt(
        total="12.00",
        line_items=(item("Milk", "2.00"), item("Bread", "9.00")),  # sums to 11.00
    )
    stage(session, accounts, FakeReader(read))

    rows = session.scalars(select(ImportCandidate)).all()
    assert len(rows) == 1
    assert rows[0].amount == Decimal("-12.00")
    assert "split_of_total" not in rows[0].raw


def test_a_zero_or_negative_line_item_stays_one_candidate(session, accounts):
    read = a_receipt(
        total="12.00",
        line_items=(item("Milk", "12.00"), item("Discount", "0.00")),
    )
    stage(session, accounts, FakeReader(read))
    assert session.scalar(select(func.count()).select_from(ImportCandidate)) == 1


def test_no_line_items_stays_one_candidate(session, accounts):
    """The default, unsplit case -- pinned explicitly so a future change to
    the split logic cannot silently start splitting everything."""
    candidate = stage(session, accounts, FakeReader(a_receipt(total="42.30")))
    assert session.scalar(select(func.count()).select_from(ImportCandidate)) == 1
    assert candidate.amount == Decimal("-42.30")


def test_each_split_candidate_gets_its_own_category_suggestion(
    session, accounts, categories, monkeypatch
):
    class _FakeCategorySuggester:
        model = "fake"

        def suggest(self, descriptions, categories):
            return {"Milk": "Groceries", "Wine": "Restaurants"}

        def verify(self, picks):
            return {}

    from app.domain import enrichment

    monkeypatch.setattr(enrichment, "build_suggester", lambda: _FakeCategorySuggester())

    read = a_receipt(
        merchant="WAITROSE",
        total="15.00",
        line_items=(item("Milk", "5.00"), item("Wine", "10.00")),
    )
    stage(session, accounts, FakeReader(read))

    rows = {r.description: r for r in session.scalars(select(ImportCandidate)).all()}
    assert rows["Milk"].suggested_category_id == categories["groceries"].id
    assert rows["Wine"].suggested_category_id == categories["restaurants"].id


def test_a_split_candidate_can_still_be_flagged_as_a_duplicate(session, accounts, categories):
    """One line item matching an already-imported payment is flagged; the
    sibling line items from the same receipt are not swept in with it."""
    post(session, date(2026, 8, 18), "Milk",
         [(accounts["current"], "-2.00"), (accounts["groceries"], "2.00")])

    read = a_receipt(
        when=date(2026, 8, 18),
        total="12.00",
        line_items=(item("Milk", "2.00"), item("Bread", "10.00")),
    )
    stage(session, accounts, FakeReader(read))

    rows = {r.description: r for r in session.scalars(select(ImportCandidate)).all()}
    assert rows["Milk"].status == CandidateStatus.DUPLICATE
    assert rows["Bread"].status == CandidateStatus.PENDING


# --------------------------------------------------------------------------
# Line-item parsing
# --------------------------------------------------------------------------


def test_line_items_are_read_from_the_reply():
    read = receipts.parse(
        '{"total":"12.00","confident":true,"line_items":'
        '[{"description":"Milk","amount":"2.00"},{"description":"Bread","amount":"10.00"}]}'
    )
    assert read.line_items == (item("Milk", "2.00"), item("Bread", "10.00"))
    assert read.splittable_items == read.line_items


def test_a_malformed_line_item_is_dropped_not_fatal():
    read = receipts.parse(
        '{"total":"12.00","confident":true,"line_items":'
        '[{"description":"Milk","amount":"banana"},{"description":"Bread","amount":"10.00"}]}'
    )
    assert read.line_items == (item("Bread", "10.00"),)


def test_missing_line_items_is_an_empty_tuple():
    read = receipts.parse('{"total":"12.00","confident":true}')
    assert read.line_items == ()
    assert read.splittable_items == ()


def test_splittable_items_is_empty_when_the_sum_is_off_by_a_penny():
    read = a_receipt(total="12.00", line_items=(item("Milk", "2.00"), item("Bread", "9.99")))
    assert read.splittable_items == ()


# --------------------------------------------------------------------------
# A3 and parsing
# --------------------------------------------------------------------------


def test_no_provider_means_no_reader(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "llm_provider", "none")
    assert isinstance(receipts.build_reader(), receipts.NullReader)


def test_an_open_model_provider_supplies_a_vision_reader(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(settings, "llm_vision_model", "llama3.2-vision")
    built = receipts.build_reader()
    assert isinstance(built, receipts.OpenAICompatibleReader)
    assert built.model == "llama3.2-vision"


def test_an_unreachable_vision_server_reads_nothing_rather_than_raising(monkeypatch):
    """Ollama not running must refuse the receipt, not crash the upload."""
    reader = receipts.OpenAICompatibleReader(
        "http://127.0.0.1:1/v1", "", "llama3.2-vision"
    )
    assert not reader.read(JPEG, "image/jpeg").usable


def test_with_no_provider_a_receipt_upload_is_refused_not_crashed(
    client, accounts, monkeypatch
):
    from app.config import settings

    monkeypatch.setattr(settings, "llm_provider", "none")
    r = client.post(
        "/api/import/receipt",
        data={"account_id": str(accounts["current"].id)},
        files={"file": ("r.jpg", JPEG, "image/jpeg")},
    )
    assert r.status_code == 422
    assert "Could not read a total" in r.json()["detail"]


def test_a_fenced_reply_is_read():
    read = receipts.parse(
        '```json\n{"merchant":"PRET","date":"2026-08-18",'
        '"total":"4.85","confident":true}\n```'
    )
    assert read.merchant == "PRET"
    assert read.total == Decimal("4.85")
    assert read.when == date(2026, 8, 18)
    assert read.confident


def test_a_total_with_a_currency_symbol_is_read():
    assert receipts.parse('{"total":"£42.30","confident":true}').total == Decimal("42.30")


def test_the_total_is_a_decimal_not_a_float():
    """A receipt total that arrives as 42.299999 is not a total."""
    read = receipts.parse('{"total":"42.30","confident":true}')
    assert isinstance(read.total, Decimal)
    assert str(read.total) == "42.30"


def test_a_nonsense_reply_is_unusable_not_an_exception():
    for text in ("sorry, I cannot read this", "", "[1,2,3]", '{"total":"banana"}'):
        read = receipts.parse(text)
        assert not read.usable


def test_a_bad_date_does_not_discard_a_good_total():
    read = receipts.parse('{"total":"12.00","date":"last tuesday","confident":true}')
    assert read.total == Decimal("12.00")
    assert read.when is None


# --------------------------------------------------------------------------
# Verification reply parsing
# --------------------------------------------------------------------------


def test_a_matching_verification_reply_is_read():
    v = receipts.parse_verification('{"matches": true, "note": ""}')
    assert v.matches is True
    assert v.note == ""


def test_a_disagreeing_verification_reply_is_read():
    v = receipts.parse_verification(
        '{"matches": false, "note": "total looks like 45.30, not 42.30"}'
    )
    assert v.matches is False
    assert "45.30" in v.note


def test_a_fenced_verification_reply_is_read():
    v = receipts.parse_verification('```json\n{"matches": true, "note": ""}\n```')
    assert v.matches is True


def test_an_unparseable_verification_reply_is_no_opinion_not_a_match():
    for text in ("sorry, I cannot help", "", "[1,2,3]"):
        assert receipts.parse_verification(text) is None


def test_an_open_model_provider_supplies_a_reader_with_verify(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "llm_provider", "openai_compatible")
    built = receipts.build_reader()
    assert hasattr(built, "verify")


def test_an_unreachable_vision_server_returns_no_opinion_rather_than_raising():
    """Same failure mode as read(): Ollama being down must not raise."""
    reader = receipts.OpenAICompatibleReader(
        "http://127.0.0.1:1/v1", "", "llama3.2-vision"
    )
    assert reader.verify(JPEG, "image/jpeg", a_receipt()) is None
