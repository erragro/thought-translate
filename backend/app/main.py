"""
app/main.py
============
Thought Translate backend — API entrypoint.

Modules: auth (signup/login/refresh/logout, RBAC, OAuth) and translate
(the Understand -> Research -> Synthesize -> Validate pipeline, Sarvam
only / Hindi<->English only for this first working slice).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from app.auth.routes import router as auth_router
from app.auth.service import ensure_auth_tables, ensure_bootstrap_admin
from app.auth.user_routes import router as user_router
from app.config import settings
from app.db import engine
from app.rate_limiter import limiter
from app.redis_client import ping as redis_ping
from app.translate.routes import router as translate_router
from app.translate.service import ensure_translate_tables

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("thought_translate")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_auth_tables()
    ensure_bootstrap_admin()
    ensure_translate_tables()
    logger.info("Thought Translate backend started.")
    yield


app = FastAPI(title="Thought Translate API", version="0.1.0", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(user_router)
app.include_router(translate_router)


@app.get("/health")
def health():
    db_ok = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error("Health check DB failure: %s", exc)
        db_ok = False

    redis_ok = redis_ping()
    status_ok = db_ok and redis_ok
    body = {"status": "ok" if status_ok else "degraded", "db": db_ok, "redis": redis_ok}
    return JSONResponse(content=body, status_code=200 if status_ok else 503)
