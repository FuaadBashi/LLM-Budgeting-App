from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.budget_routes import router as budget_router
from app.api.auth_routes import router as auth_router
from app.api.export_routes import router as export_router
from app.api.obligation_routes import router as obligation_router
from app.api.routes import router
from app.auth import require_session, startup_warning

@asynccontextmanager
async def lifespan(_: FastAPI):
    # The insecure default must announce itself rather than be inferred from
    # the absence of a login screen.
    message = startup_warning()
    if message:
        logging.getLogger("uvicorn.error").warning(message)
    yield


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



