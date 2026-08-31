from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.backup_routes import router as backup_router
from app.api.budget_routes import router as budget_router
from app.api.auth_routes import router as auth_router
from app.api.export_routes import router as export_router
from app.api.goal_routes import router as goal_router
from app.api.obligation_routes import router as obligation_router
from app.api.routes import router
from app.api.import_routes import router as import_router
from app.api.insight_routes import router as insight_router
from app.api.scenario_routes import router as scenario_router
from app import scheduler
from app.auth import require_session, session_secret_warning, startup_warning

@asynccontextmanager
async def lifespan(_: FastAPI):
    # The insecure default must announce itself rather than be inferred from
    # the absence of a login screen.
    log = logging.getLogger("uvicorn.error")
    for message in (startup_warning(), session_secret_warning()):
        if message:
            log.warning(message)
    # The timer only runs while this process does. That limitation is why
    # `/backups` reports the age of the newest file rather than just "on".
    task = scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop(task)


app = FastAPI(
    lifespan=lifespan,
    title="Personal Finance OS",
    description="Ledger-first personal finance platform. See docs/FINANCIAL_RULEBOOK.md.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    # Cookies must be allowed through, or the session never reaches the API.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Public: health and the login endpoints. Everything else needs a session.
app.include_router(auth_router, prefix="/api")
app.include_router(router, prefix="/api", dependencies=[Depends(require_session)])
app.include_router(budget_router, prefix="/api", dependencies=[Depends(require_session)])
app.include_router(obligation_router, prefix="/api", dependencies=[Depends(require_session)])
app.include_router(export_router, prefix="/api", dependencies=[Depends(require_session)])
app.include_router(goal_router, prefix="/api", dependencies=[Depends(require_session)])
app.include_router(scenario_router, prefix="/api", dependencies=[Depends(require_session)])
app.include_router(import_router, prefix="/api", dependencies=[Depends(require_session)])
app.include_router(insight_router, prefix="/api", dependencies=[Depends(require_session)])
app.include_router(backup_router, prefix="/api", dependencies=[Depends(require_session)])



