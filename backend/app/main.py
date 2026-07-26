"""Silo FastAPI application entrypoint.

Run locally:
    uvicorn app.main:app --reload

Run in production (Render):
    uvicorn app.main:app --host 0.0.0.0 --port $PORT

Swagger docs are served automatically by FastAPI at /docs (and /redoc).
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from .database import engine
from . import models
from .routers import auth, payslips, envelopes, transactions, reports, admin, payments

# Create tables (use Alembic migrations in production instead of this)
models.Base.metadata.create_all(bind=engine)

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="Silo API",
    description="Deterministic, rule-based salary-envelope budgeting API. No AI or ML is used anywhere in this service.",
    version="1.0.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS origins come from ALLOWED_ORIGINS (comma-separated), e.g.
#   ALLOWED_ORIGINS=https://silo.netlify.app,https://silo.com
# Falls back to "*" for local development only — set this env var on Render
# once you know your Netlify URL, since credentialed wildcard CORS is
# rejected by browsers and isn't safe for production anyway.
_origins_env = os.getenv("ALLOWED_ORIGINS", "*")
allowed_origins = [o.strip() for o in _origins_env.split(",") if o.strip()]
allow_wildcard = allowed_origins == ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=not allow_wildcard,  # browsers reject credentials+wildcard together
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(payslips.router)
app.include_router(envelopes.router)
app.include_router(transactions.router)
app.include_router(reports.router)
app.include_router(admin.router)
app.include_router(payments.router)


@app.get("/health", tags=["system"])
def health_check():
    return {"status": "ok"}
