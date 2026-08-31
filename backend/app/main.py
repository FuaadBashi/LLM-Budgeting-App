from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.budget_routes import router as budget_router
from app.api.export_routes import router as export_router
from app.api.obligation_routes import router as obligation_router
from app.api.routes import router

app = FastAPI(
    title="Personal Finance OS",
    description="Ledger-first personal finance platform. See docs/FINANCIAL_RULEBOOK.md.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
app.include_router(budget_router, prefix="/api")
app.include_router(obligation_router, prefix="/api")
app.include_router(export_router, prefix="/api")
