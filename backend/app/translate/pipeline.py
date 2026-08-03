"""
app/translate/pipeline.py
===========================
The Understand -> Research -> Synthesize -> Validate -> Respond pipeline
from CONCEPT.md, v1 scope: Sarvam only, Hindi<->English only.

Rebuilt 2026-08-03 around a real finding: Sarvam's chat-completions
model (sarvam-105b) is a reasoning model that burns ~1500+ completion
tokens on chain-of-thought before answering, even for a 9-word sentence
— expensive and slow for a job that Sarvam's dedicated /translate
endpoint already does well, cheaply, with a `mode` parameter that
directly satisfies CONCEPT.md §7 (mode="formal" tested clean: common,
grammatically-correct, non-archaic Hindi).

Honest about what's real vs. deferred in this slice:
  - Synthesize: real call to Sarvam's /translate endpoint.
  - Understand, Research: no-ops for v1. The dedicated /translate model
    already handles idioms well on its own (confirmed by direct testing
    — it correctly avoided a literal "cats and dogs" translation
    unprompted), and running a separate reasoning-model call just to
    extract meaning/tone would reintroduce the token cost this redesign
    was meant to avoid. Revisit if /translate's idiom handling proves
    insufficient in practice.
  - Validate: terminology check is real, and violations are now FIXED
    deterministically (find/replace against the known archaic->common
    mapping) rather than sent back for an expensive LLM retry — we
    already know the exact correction, no need to ask a model for it.
    Conciseness check is a rough heuristic, logged not enforced.
  - Results are cached (see cache.py) — a repeated identical request
    skips Sarvam entirely.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from app.translate.cache import get_cached, set_cached
from app.translate.sarvam_client import get_sarvam_client
from app.translate.terminology import find_archaic_terms

logger = logging.getLogger("thought_translate.pipeline")

# A translation is flagged (not failed) if it's more than this many times
# longer than the source, character-for-character.
_CONCISENESS_RATIO_FLAG = 1.8


@dataclass
class StageRecord:
    stage: str
    input_json: dict
    output_json: dict
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


@dataclass
class PipelineResult:
    translated_text: str
    translator_notes: str
    stage_records: list[StageRecord] = field(default_factory=list)
    from_cache: bool = False


def _understand_stub() -> StageRecord:
    return StageRecord(
        stage="understand",
        input_json={},
        output_json={"status": "skipped", "reason": "handled internally by Sarvam's /translate model — see pipeline.py docstring"},
    )


def _research_stub() -> StageRecord:
    return StageRecord(
        stage="research",
        input_json={},
        output_json={"status": "skipped", "reason": "grammar-rules + translation-guidelines reference corpus not built yet"},
    )


def _synthesize(
    client,
    source_lang: str,
    target_lang: str,
    source_text: str,
    mode: str,
    output_script: str | None,
    numerals_format: str,
) -> tuple[str, StageRecord]:
    result = client.translate(
        source_lang=source_lang,
        target_lang=target_lang,
        text=source_text,
        mode=mode,
        output_script=output_script,
        numerals_format=numerals_format,
    )
    record = StageRecord(
        stage="synthesize",
        input_json={
            "source_text": source_text,
            "mode": mode,
            "output_script": output_script,
            "numerals_format": numerals_format,
        },
        output_json={"translation": result.translated_text},
        latency_ms=result.latency_ms,
    )
    return result.translated_text, record


def _validate_and_fix(source_text: str, translation: str) -> tuple[str, dict, StageRecord]:
    hits = find_archaic_terms(translation)
    fixed = translation
    for hit in hits:
        fixed = fixed.replace(hit["found"], hit["suggested"])

    ratio = (len(fixed) / len(source_text)) if source_text else 0
    output = {
        "terminology_violations": hits,
        "terminology_fixed": fixed != translation,
        "length_ratio": round(ratio, 2),
        "conciseness_flag": ratio > _CONCISENESS_RATIO_FLAG,
    }
    record = StageRecord(
        stage="validate",
        input_json={"translation": translation},
        output_json=output,
    )
    return fixed, output, record


_LANGUAGE_NAMES = {"en": "English", "hi": "Hindi"}


def _extract_json(raw: str) -> dict:
    """LLM output should be pure JSON per the prompt, but be lenient —
    grab the first {...} block if there's stray text around it."""
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            pass
    logger.warning("Could not parse JSON from LLM output: %r", raw[:300])
    return {}


def _parse_indexed_replacements(raw: str) -> dict[int, str]:
    """Expects {"replacements": [{"item": 1, "text": "..."}, ...]}. Keyed
    by an explicit item number rather than array position — a reasoning
    model asked to produce several replacements at once can reorder,
    skip, or miscount its own array, and matching by position alone
    would silently pair replacement #2 with flagged span #1's offsets,
    splicing unrelated text into the wrong place. Returns only the
    entries that parsed cleanly; a partial/malformed response still
    yields whatever's usable instead of being discarded wholesale."""
    parsed = _extract_json(raw)
    replacements = parsed.get("replacements") if isinstance(parsed, dict) else None
    if not isinstance(replacements, list):
        return {}
    result: dict[int, str] = {}
    for entry in replacements:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("item")
        text = entry.get("text")
        if isinstance(idx, int) and isinstance(text, str) and text.strip():
            result[idx] = text.strip()
    return result


def revise_translation_multi(
    source_lang: str,
    target_lang: str,
    source_text: str,
    current_translation: str,
    feedback_items: list[dict],
) -> tuple[str, StageRecord]:
    """Comment-triggered revision (CONCEPT.md's revision loop).

    Targeted-patch design (2026-08-03, replacing an earlier whole-
    paragraph-regeneration approach): asking the reasoning model to
    regenerate the ENTIRE paragraph invited exactly the kind of open-
    ended deliberation that burns through its token budget — confirmed
    by direct testing (chain-of-thought alone routinely exceeded the
    account's 4096-token ceiling, sometimes producing no answer at all).
    Feeding it MORE context (e.g. the whole document) would only make
    that worse, not better — more to reason about, not less.

    Instead: ask ONLY for a replacement phrase per flagged span — a
    narrow, local, "fill in the blank" task — and splice it in
    ourselves by position (span_start/span_end, applied right-to-left so
    earlier offsets stay valid). Smaller ask, smaller output, less
    reasoning surface. Trade-off, stated plainly: a pure splice doesn't
    see the whole sentence when producing a replacement, so it can miss
    a grammar-agreement change elsewhere the fix implies — accepted for
    now given the alternative was "sometimes produces nothing at all."

    Reworked 2026-08-03 after two real bugs surfaced in production use:
    (1) matching replacements to spans by array position let a
    reasoning model's own reordering/miscounting splice the wrong text
    into the wrong place — fixed by having the model tag each
    replacement with an explicit item number and matching by that
    instead of position; (2) any single unusable response discarded the
    ENTIRE batch, even when some items would have applied fine — fixed
    by applying whatever items succeed independently, with a retry
    (fresh sample, since the same model can produce a usable response
    on a second attempt) only for the case where NOTHING parsed at all.

    `feedback_items`: each dict needs quoted_text, comment_text,
    category, span_start, span_end (character offsets into
    current_translation)."""
    client = get_sarvam_client()
    system = (
        f"You are given a {_LANGUAGE_NAMES.get(target_lang, target_lang)} translation and specific "
        "feedback about certain flagged phrases within it. For EACH flagged phrase, produce ONLY its "
        "direct replacement — do not rewrite the sentence around it. The replacement must read naturally "
        "in place of the flagged text, use common contemporary vocabulary (not archaic or bureaucratic), "
        "and stay concise. Match the script and numeral style already used in the surrounding text "
        "(for example, if it's written in Roman letters, reply in Roman letters; if it uses native-script "
        "digits, keep using native-script digits). Respond with ONLY a JSON object shaped exactly like: "
        '{"replacements": [{"item": 1, "text": "replacement for item 1"}, {"item": 2, "text": "..."}]} '
        "— include every item number listed below exactly once, tagged with its own number so it's clear "
        "which replacement belongs to which flagged phrase. No other text."
    )
    items_block = "\n".join(
        f'{i + 1}. Flagged text: "{f["quoted_text"]}" — Feedback: {f["comment_text"]} ({f["category"]})'
        for i, f in enumerate(feedback_items)
    )
    user = (
        f"Full current translation: {current_translation}\n\n"
        f"Flagged phrases and feedback:\n{items_block}\n\n"
        "Produce ONLY the replacement text for each flagged phrase, each tagged with its item number."
    )

    replacements_by_item: dict[int, str] = {}
    result = None
    for attempt in range(2):
        result = client.chat(role="smart", system=system, user=user, max_tokens=4096, temperature=0.3)
        replacements_by_item = _parse_indexed_replacements(result.content)
        if replacements_by_item:
            break
        logger.warning(
            "Targeted revision attempt %d/2 produced nothing usable: %r", attempt + 1, result.content[:300]
        )

    # Apply right-to-left (by span_start descending) so an edit never
    # shifts the offsets of an edit still waiting to be applied. Each
    # item is independent — one missing/invalid replacement just leaves
    # that specific span untouched, not the whole batch.
    ordered = sorted(enumerate(feedback_items, start=1), key=lambda pair: pair[1]["span_start"], reverse=True)
    text = current_translation
    applied_ranges: list[tuple[int, int]] = []
    item_results = []
    for item_num, fb in ordered:
        repl = replacements_by_item.get(item_num)
        start, end = fb["span_start"], fb["span_end"]
        overlaps = any(not (end <= a_start or start >= a_end) for a_start, a_end in applied_ranges)
        valid_span = 0 <= start < end <= len(text)
        if repl and valid_span and not overlaps:
            text = text[:start] + repl + text[end:]
            applied_ranges.append((start, end))
            item_results.append({"item": item_num, "applied": True})
        else:
            reason = "no_replacement" if not repl else ("overlap" if overlaps else "invalid_span")
            item_results.append({"item": item_num, "applied": False, "reason": reason})

    any_applied = bool(applied_ranges)
    revised = text if any_applied else current_translation
    if not any_applied:
        logger.warning("No targeted revision applied for any of %d feedback items.", len(feedback_items))

    record = StageRecord(
        stage="revise",
        input_json={"current_translation": current_translation, "feedback_items": feedback_items},
        output_json={"translation": revised, "item_results": item_results, "fallback_to_prior": not any_applied},
        prompt_tokens=result.prompt_tokens if result else 0,
        completion_tokens=result.completion_tokens if result else 0,
        latency_ms=result.latency_ms if result else 0,
    )
    return revised, record


def revise_translation(
    source_lang: str,
    target_lang: str,
    source_text: str,
    current_translation: str,
    quoted_text: str,
    comment_text: str,
    category: str,
    span_start: int,
    span_end: int,
) -> tuple[str, StageRecord]:
    """Single-comment case (paste mode) — thin wrapper over the multi-
    feedback version above."""
    return revise_translation_multi(
        source_lang,
        target_lang,
        source_text,
        current_translation,
        [
            {
                "quoted_text": quoted_text,
                "comment_text": comment_text,
                "category": category,
                "span_start": span_start,
                "span_end": span_end,
            }
        ],
    )


def run_pipeline(
    source_lang: str,
    target_lang: str,
    source_text: str,
    client=None,
    mode: str | None = None,
    output_script: str | None = "fully-native",
    numerals_format: str = "international",
) -> PipelineResult:
    """`client` is optional — pass a shared SarvamClient when translating
    many chunks in one request (document upload) so each chunk doesn't
    pay the cost of spinning up a fresh HTTP client. `mode` defaults to
    the account-wide setting when not given per-request."""
    from app.config import settings

    mode = mode or settings.sarvam_translate_mode

    cached = get_cached(source_lang, target_lang, mode, output_script, numerals_format, source_text)
    if cached:
        return PipelineResult(
            translated_text=cached["translation"],
            translator_notes=cached.get("notes", ""),
            stage_records=[
                StageRecord(stage="cache", input_json={}, output_json={"status": "hit"}),
            ],
            from_cache=True,
        )

    client = client or get_sarvam_client()
    stage_records: list[StageRecord] = [_understand_stub(), _research_stub()]

    draft, rec = _synthesize(client, source_lang, target_lang, source_text, mode, output_script, numerals_format)
    stage_records.append(rec)

    final_text, _validation, rec = _validate_and_fix(source_text, draft)
    stage_records.append(rec)

    set_cached(
        source_lang,
        target_lang,
        mode,
        output_script,
        numerals_format,
        source_text,
        {"translation": final_text, "notes": ""},
    )

    return PipelineResult(
        translated_text=final_text,
        translator_notes="",
        stage_records=stage_records,
    )
