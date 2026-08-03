"""
app/translate/sarvam_client.py
================================
Two Sarvam AI endpoints, for two different jobs:

  - chat()      — OpenAI-compatible /v1/chat/completions. Ported from
                   quickbites-bot's SarvamProvider. sarvam-105b is a
                   REASONING model — confirmed via direct testing
                   (2026-08-03) that it burns ~1500+ completion tokens on
                   chain-of-thought before any answer, even for a 9-word
                   sentence. Kept for stages that genuinely need general
                   reasoning, not used for the core translate call.
  - translate()  — the dedicated /translate endpoint. No reasoning tax,
                   purpose-built, and its `mode` parameter directly
                   addresses the register/terminology requirement in
                   CONCEPT.md §7 (mode="formal" tested clean — common,
                   grammatically-correct Hindi, no archaic terms, no
                   code-mixing). This is what Synthesize actually calls.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger("thought_translate.sarvam")

_CHAT_URL = "https://api.sarvam.ai/v1/chat/completions"
_TRANSLATE_URL = "https://api.sarvam.ai/translate"

# Sarvam's /translate endpoint wants BCP-47-ish codes (en-IN, hi-IN), not
# the bare "en"/"hi" the rest of this app uses.
_LANGUAGE_CODES = {"en": "en-IN", "hi": "hi-IN"}

# mayura:v1's hard input cap, confirmed 2026-08-03 via Sarvam's own docs
# ("The maximum is 1000 characters for Mayura:v1"). Both paste mode
# (routes.py) and document mode's per-chunk sub-splitting
# (document_pipeline.py) key off this single constant.
MAX_TRANSLATE_INPUT_CHARS = 1000


@dataclass
class ChatResult:
    content: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


@dataclass
class TranslateResult:
    translated_text: str
    latency_ms: int


class SarvamClient:
    def __init__(self, api_key: str, fast_model: str, smart_model: str):
        self._api_key = api_key
        self._model_by_role = {"fast": fast_model, "smart": smart_model}
        self._client = httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0))

    def _resolve(self, role: str) -> str:
        return self._model_by_role.get(role, self._model_by_role["smart"])

    def _post_with_retry(self, url: str, body: dict, headers: dict, max_attempts: int = 4) -> dict:
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                resp = self._client.post(url, json=body, headers=headers)
                if resp.status_code == 429 or 500 <= resp.status_code < 600:
                    retry_after = resp.headers.get("retry-after")
                    delay = (
                        min(20.0, float(retry_after))
                        if retry_after and retry_after.isdigit()
                        else min(16.0, (2**attempt) + random.uniform(0, 1))
                    )
                    logger.warning(
                        "sarvam %s on attempt %d/%d; sleeping %.1fs",
                        resp.status_code, attempt, max_attempts, delay,
                    )
                    time.sleep(delay)
                    continue
                resp.raise_for_status()
                return resp.json()
            except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError) as exc:
                last_exc = exc
                delay = min(16.0, (2**attempt) + random.uniform(0, 1))
                logger.warning(
                    "sarvam transport error on attempt %d/%d (%s); sleeping %.1fs",
                    attempt, max_attempts, type(exc).__name__, delay,
                )
                time.sleep(delay)
        if last_exc:
            raise last_exc
        raise RuntimeError("sarvam: retries exhausted")

    def chat(
        self,
        *,
        role: str,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> ChatResult:
        body = {
            "model": self._resolve(role),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        started = time.monotonic()
        data = self._post_with_retry(_CHAT_URL, body, headers)
        latency_ms = int((time.monotonic() - started) * 1000)

        content = ""
        choices = data.get("choices") if isinstance(data, dict) else None
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") or {}
            if isinstance(msg.get("content"), str):
                content = msg["content"]
        if not content:
            logger.warning("Sarvam returned unexpected shape: %r", str(data)[:300])

        usage = data.get("usage") or {} if isinstance(data, dict) else {}
        return ChatResult(
            content=content,
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            latency_ms=latency_ms,
        )

    def translate(
        self,
        *,
        source_lang: str,
        target_lang: str,
        text: str,
        mode: str = "formal",
        output_script: str | None = "fully-native",
        numerals_format: str = "international",
    ) -> TranslateResult:
        """Calls the dedicated /translate endpoint — no reasoning tax,
        `mode` controls register (see module docstring). Explicitly pins
        `model: mayura:v1` (confirmed 2026-08-03 via Sarvam's docs) —
        `output_script` (roman/native transliteration) and
        `numerals_format` (native-digit vs 0-9) are ONLY supported by
        mayura:v1, not sarvam-translate:v1, and both Hindi and English
        are in mayura's 12 supported languages."""
        body = {
            "input": text,
            "source_language_code": _LANGUAGE_CODES.get(source_lang, source_lang),
            "target_language_code": _LANGUAGE_CODES.get(target_lang, target_lang),
            "model": "mayura:v1",
            "mode": mode,
            "numerals_format": numerals_format,
        }
        if output_script:
            body["output_script"] = output_script
        headers = {
            "api-subscription-key": self._api_key,
            "Content-Type": "application/json",
        }
        started = time.monotonic()
        data = self._post_with_retry(_TRANSLATE_URL, body, headers)
        latency_ms = int((time.monotonic() - started) * 1000)

        translated = data.get("translated_text", "") if isinstance(data, dict) else ""
        if not translated:
            logger.warning("Sarvam /translate returned unexpected shape: %r", str(data)[:300])

        return TranslateResult(translated_text=translated, latency_ms=latency_ms)


def get_sarvam_client() -> SarvamClient:
    return SarvamClient(
        api_key=settings.sarvam_api_key,
        fast_model=settings.sarvam_fast_model,
        smart_model=settings.sarvam_smart_model,
    )
