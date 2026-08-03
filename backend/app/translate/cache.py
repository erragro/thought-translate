"""
app/translate/cache.py
========================
Redis-backed cache for translation results, keyed by
(source_lang, target_lang, mode, normalized text). Avoids re-calling
Sarvam (and re-paying/re-waiting) for a repeated identical request.

Not a cache of raw LLM tokens/prompts — a cache of the finished,
Validate-passed translation. Simpler, and it's what actually saves cost
here since /translate is billed per call, not per token.
"""

from __future__ import annotations

import hashlib
import json
import logging

from app.config import settings
from app.redis_client import get_redis

logger = logging.getLogger("thought_translate.translate_cache")

_KEY_PREFIX = "translate:cache:"


def _cache_key(
    source_lang: str, target_lang: str, mode: str, output_script: str | None, numerals_format: str, text: str
) -> str:
    normalized = text.strip().lower()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    variant = f"{mode}:{output_script or 'none'}:{numerals_format}"
    return f"{_KEY_PREFIX}{source_lang}:{target_lang}:{variant}:{digest}"


def get_cached(
    source_lang: str, target_lang: str, mode: str, output_script: str | None, numerals_format: str, text: str
) -> dict | None:
    try:
        raw = get_redis().get(_cache_key(source_lang, target_lang, mode, output_script, numerals_format, text))
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.warning("Translation cache read failed (continuing without cache): %s", exc)
        return None


def set_cached(
    source_lang: str,
    target_lang: str,
    mode: str,
    output_script: str | None,
    numerals_format: str,
    text: str,
    value: dict,
) -> None:
    try:
        get_redis().set(
            _cache_key(source_lang, target_lang, mode, output_script, numerals_format, text),
            json.dumps(value),
            ex=settings.translate_cache_ttl_seconds,
        )
    except Exception as exc:
        logger.warning("Translation cache write failed (continuing without cache): %s", exc)
