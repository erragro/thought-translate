"""
app/redis_client.py
====================
Redis client factory — single-node only (no cluster mode; not needed
at this scale). Used for slowapi rate limiting and login-lockout counters.
"""

from __future__ import annotations

import logging

import redis

from app.config import settings

logger = logging.getLogger(__name__)

_pool = redis.ConnectionPool.from_url(
    settings.redis_url,
    max_connections=settings.redis_max_connections,
    decode_responses=True,
)


def get_redis() -> redis.Redis:
    return redis.Redis(connection_pool=_pool)


def ping() -> bool:
    try:
        return bool(get_redis().ping())
    except Exception:
        return False
