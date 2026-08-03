"""
app/rate_limiter.py
====================
Shared slowapi limiter instance — imported by both main.py and route
modules, kept here to avoid circular imports.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings

limiter = Limiter(key_func=get_remote_address, storage_uri=settings.redis_url)
