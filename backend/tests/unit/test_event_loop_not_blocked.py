"""Slow synchronous work must not freeze the event loop.

This is the test class that did not exist when two real bugs shipped:

* ``GET /insights`` called a local model synchronously in the request path
  and took 10.8 seconds per page load.
* ``POST /import`` and ``POST /import/receipt`` are ``async def`` but called
  fully synchronous ``httpx`` code -- a duplicate second-opinion, a
  categorisation pass, its verify pass and a canonical-name pass. Measured at
  ~55 seconds for a statement with 20 new merchants against the local model,
  with a 120s-per-call ceiling.

The second is the worse defect and the reason this file exists. An ``async
def`` route body runs *on the single event loop thread*, so a blocking call
inside one does not merely make that request slow -- it stops every other
request in the process for the same duration. A test that only measured the
upload's own latency would have called that "an upload is slow, as expected"
and missed it entirely.

So these tests assert the thing that actually matters: a second, unrelated
request completes *while* the slow one is still in flight.
"""

from __future__ import annotations

import time
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import get_session
from app.main import app

ISO = "date,description,amount\n2026-08-04,TESCO STORES 3421,-62.40\n"

#: Long enough that a blocked loop is unambiguous, short enough to keep the
#: suite fast. A blocked event loop makes the concurrent request take at
#: least this long; a healthy one answers it in milliseconds.
STALL = 0.6


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def async_client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    app.dependency_overrides.clear()


async def _concurrent_probe(client, slow_request):
    """Run `slow_request` and, concurrently, a trivial GET.

    Returns (probe_finished_at, slow_response) where `probe_finished_at` is
    measured from the moment the slow request was launched.

    Measuring the probe's *own* duration does not work and is worth spelling
    out, because the obvious version of this test passes whether or not the
    bug is present: when the loop is blocked the probe cannot even begin
    until the block clears, so it starts its stopwatch late and records a
    fast request. What actually distinguishes the two worlds is *when the
    probe finishes on the wall clock* -- ~0.05s if the loop stayed free,
    ~STALL if the probe had to wait its turn.
    """
    import anyio

    finished = {}
    slow_result = {}
    t0 = time.perf_counter()

    async def probe():
        # Let the slow request reach its blocking section first.
        await anyio.sleep(0.05)
        await client.get("/api/auth/session")
        finished["at"] = time.perf_counter() - t0

    async with anyio.create_task_group() as tg:
        tg.start_soon(probe)
        slow_result["r"] = await slow_request()

    return finished["at"], slow_result["r"]


@pytest.mark.anyio
async def test_a_slow_statement_import_does_not_freeze_other_requests(
    async_client, accounts, session, monkeypatch
):
    """POST /import offloads to a threadpool, so the API stays responsive.

    Without `run_in_threadpool` in the route this fails: the probe waits the
    full STALL because the event loop is inside a blocking call.
    """
    from app.domain import importing

    real_stage = importing.stage

    def slow_stage(*args, **kwargs):
        time.sleep(STALL)  # stands in for the blocking httpx calls
        return real_stage(*args, **kwargs)

    monkeypatch.setattr(importing, "stage", slow_stage)

    async def upload():
        return await async_client.post(
            "/api/import",
            data={"account_id": str(accounts["current"].id)},
            files={"file": ("s.csv", ISO.encode(), "text/csv")},
        )

    probe_finished_at, response = await _concurrent_probe(async_client, upload)

    assert response.status_code == 201
    assert probe_finished_at < STALL / 2, (
        f"an unrelated request only completed {probe_finished_at:.2f}s after an "
        f"import started (the import itself stalls for {STALL}s) -- the event "
        f"loop was blocked, so the whole API freezes for the length of every "
        f"upload, not just this request"
    )


@pytest.mark.anyio
async def test_a_slow_receipt_read_does_not_freeze_other_requests(
    async_client, accounts, session, monkeypatch
):
    """POST /import/receipt is the worse path: a vision read plus a verify
    pass, both blocking, on a model slower than the text one."""
    from app.domain import receipts

    def slow_stage(*args, **kwargs):
        time.sleep(STALL)
        raise receipts.ReceiptError("could not read")  # shape is irrelevant here

    monkeypatch.setattr(receipts, "stage", slow_stage)

    async def upload():
        return await async_client.post(
            "/api/import/receipt",
            data={"account_id": str(accounts["current"].id)},
            files={"file": ("r.jpg", b"\xff\xd8\xff\xe0fake", "image/jpeg")},
        )

    probe_finished_at, response = await _concurrent_probe(async_client, upload)

    assert response.status_code == 422
    assert probe_finished_at < STALL / 2, (
        f"an unrelated request only completed {probe_finished_at:.2f}s after a "
        f"receipt read started -- the event loop was blocked"
    )
