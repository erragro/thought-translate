"""
app/translate/service.py
==========================
DB persistence for the translate module. Schema per CONCEPT.md's
"Resolved via best practices" §A — one `pipeline_stage_runs` table
deliberately serves both token-usage tracking (§4) and the audit trace
(§6), rather than building two overlapping logging systems.

`translation_comments` now exists (span-annotation editor, §3), and a
comment now triggers a new `translation_versions` row (the revision
loop) — `version_number` increments per thread, so a thread is a real
version history, not just one row. `correction_examples` (the
distillation corpus, §5/§6) still doesn't exist — that's the next layer
on top of this (turning each revision into a labeled training example),
not built yet.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import text

from app.db import get_db_session
from app.translate.document_pipeline import DocumentResult
from app.translate.pipeline import PipelineResult, StageRecord

logger = logging.getLogger("thought_translate.translate_service")


def ensure_translate_tables() -> None:
    ddl = """
        CREATE TABLE IF NOT EXISTS translation_threads (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            source_lang VARCHAR(10) NOT NULL,
            target_lang VARCHAR(10) NOT NULL,
            input_mode VARCHAR(20) NOT NULL DEFAULT 'paste',
            original_source_text TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS translation_versions (
            id SERIAL PRIMARY KEY,
            thread_id INTEGER NOT NULL REFERENCES translation_threads(id) ON DELETE CASCADE,
            version_number INTEGER NOT NULL DEFAULT 1,
            translated_text TEXT NOT NULL,
            translator_notes TEXT NOT NULL DEFAULT '',
            status VARCHAR(20) NOT NULL DEFAULT 'draft',
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        ALTER TABLE translation_versions ADD COLUMN IF NOT EXISTS structured_output JSONB;
        ALTER TABLE translation_threads ADD COLUMN IF NOT EXISTS original_structured JSONB;
        ALTER TABLE translation_threads ADD COLUMN IF NOT EXISTS original_filename TEXT;

        CREATE TABLE IF NOT EXISTS pipeline_stage_runs (
            id SERIAL PRIMARY KEY,
            version_id INTEGER NOT NULL REFERENCES translation_versions(id) ON DELETE CASCADE,
            stage VARCHAR(20) NOT NULL,
            provider VARCHAR(20) NOT NULL DEFAULT 'sarvam',
            input_json JSONB NOT NULL DEFAULT '{}',
            output_json JSONB NOT NULL DEFAULT '{}',
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            latency_ms INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS translation_comments (
            id SERIAL PRIMARY KEY,
            version_id INTEGER NOT NULL REFERENCES translation_versions(id) ON DELETE CASCADE,
            span_start INTEGER NOT NULL,
            span_end INTEGER NOT NULL,
            quoted_text TEXT NOT NULL DEFAULT '',
            comment_text TEXT NOT NULL,
            category VARCHAR(20) NOT NULL DEFAULT 'accuracy',
            status VARCHAR(20) NOT NULL DEFAULT 'open',
            created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            resolved_at TIMESTAMPTZ
        );

        ALTER TABLE translation_comments ADD COLUMN IF NOT EXISTS chunk_index INTEGER;

        -- Distillation corpus (CONCEPT.md §5/§6): one row per comment that
        -- fed into an actual revision. Flat (not nested JSON) on purpose —
        -- quoted_text/comment_text/category live at the top level so
        -- "has this exact phrase/feedback recurred before" is a plain
        -- GROUP BY, not a jsonb_array_elements unpack.
        CREATE TABLE IF NOT EXISTS correction_examples (
            id SERIAL PRIMARY KEY,
            thread_id INTEGER NOT NULL REFERENCES translation_threads(id) ON DELETE CASCADE,
            version_before_id INTEGER REFERENCES translation_versions(id) ON DELETE SET NULL,
            version_after_id INTEGER REFERENCES translation_versions(id) ON DELETE SET NULL,
            comment_id INTEGER REFERENCES translation_comments(id) ON DELETE SET NULL,
            chunk_index INTEGER,
            source_lang VARCHAR(10) NOT NULL,
            target_lang VARCHAR(10) NOT NULL,
            source_text TEXT NOT NULL,
            mt_output TEXT NOT NULL,
            corrected_output TEXT NOT NULL,
            quoted_text TEXT NOT NULL,
            comment_text TEXT NOT NULL,
            category VARCHAR(20) NOT NULL,
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            rating VARCHAR(10),
            rated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            rated_at TIMESTAMPTZ
        );

        -- The model's own one-sentence explanation for why it made (or
        -- declined to make) this specific edit, surfaced in the UI so
        -- a reviewer can see the reasoning behind a fix, not just the
        -- result.
        ALTER TABLE correction_examples ADD COLUMN IF NOT EXISTS reasoning TEXT;
    """
    try:
        with get_db_session() as session:
            session.execute(text(ddl))
        logger.info("Translate tables verified / created.")
    except Exception as exc:
        logger.error("Failed to ensure translate tables: %s", exc)


def _to_jsonb(value) -> str:
    return json.dumps(value)


def _persist_stage_runs(session, version_id: int, stage_records: list[StageRecord]) -> tuple[int, int]:
    total_prompt_tokens = 0
    total_completion_tokens = 0
    for rec in stage_records:
        session.execute(
            text("""
                INSERT INTO pipeline_stage_runs
                    (version_id, stage, provider, input_json, output_json,
                     prompt_tokens, completion_tokens, latency_ms)
                VALUES (:vid, :stage, 'sarvam', :input_json, :output_json,
                        :prompt_tokens, :completion_tokens, :latency_ms)
            """),
            {
                "vid": version_id,
                "stage": rec.stage,
                "input_json": _to_jsonb(rec.input_json),
                "output_json": _to_jsonb(rec.output_json),
                "prompt_tokens": rec.prompt_tokens,
                "completion_tokens": rec.completion_tokens,
                "latency_ms": rec.latency_ms,
            },
        )
        total_prompt_tokens += rec.prompt_tokens
        total_completion_tokens += rec.completion_tokens
    return total_prompt_tokens, total_completion_tokens


def save_translation(
    user_id: int,
    source_lang: str,
    target_lang: str,
    input_mode: str,
    source_text: str,
    result: PipelineResult,
) -> dict:
    with get_db_session() as session:
        thread_row = session.execute(
            text("""
                INSERT INTO translation_threads
                    (user_id, source_lang, target_lang, input_mode, original_source_text)
                VALUES (:uid, :src, :tgt, :mode, :text)
                RETURNING id
            """),
            {"uid": user_id, "src": source_lang, "tgt": target_lang, "mode": input_mode, "text": source_text},
        ).mappings().first()
        thread_id = thread_row["id"]

        version_row = session.execute(
            text("""
                INSERT INTO translation_versions
                    (thread_id, version_number, translated_text, translator_notes, status)
                VALUES (:tid, 1, :text, :notes, 'draft')
                RETURNING id
            """),
            {"tid": thread_id, "text": result.translated_text, "notes": result.translator_notes},
        ).mappings().first()
        version_id = version_row["id"]

        total_prompt_tokens, total_completion_tokens = _persist_stage_runs(session, version_id, result.stage_records)

    return {
        "thread_id": thread_id,
        "version_id": version_id,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
    }


# ---------------------------------------------------------------------------
# Version history / revisions (comment-triggered, CONCEPT.md revision loop)
# ---------------------------------------------------------------------------


def get_version(version_id: int) -> dict | None:
    with get_db_session() as session:
        row = session.execute(
            text("""
                SELECT id, thread_id, version_number, translated_text, translator_notes,
                       structured_output, created_at
                FROM translation_versions WHERE id = :vid
            """),
            {"vid": version_id},
        ).mappings().first()
    return dict(row) if row else None


def get_thread(thread_id: int) -> dict | None:
    with get_db_session() as session:
        row = session.execute(
            text("""
                SELECT id, user_id, source_lang, target_lang, input_mode,
                       original_source_text, original_structured, original_filename
                FROM translation_threads WHERE id = :tid
            """),
            {"tid": thread_id},
        ).mappings().first()
    return dict(row) if row else None


def list_versions(thread_id: int) -> list[dict]:
    with get_db_session() as session:
        rows = session.execute(
            text("""
                SELECT id, version_number, translated_text, translator_notes, created_at
                FROM translation_versions WHERE thread_id = :tid
                ORDER BY version_number ASC
            """),
            {"tid": thread_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def get_latest_version(thread_id: int) -> dict | None:
    with get_db_session() as session:
        row = session.execute(
            text("""
                SELECT id, thread_id, version_number, translated_text, translator_notes, structured_output
                FROM translation_versions WHERE thread_id = :tid
                ORDER BY version_number DESC
                LIMIT 1
            """),
            {"tid": thread_id},
        ).mappings().first()
    return dict(row) if row else None


def save_revision(thread_id: int, translated_text: str, stage_records: list[StageRecord]) -> dict:
    with get_db_session() as session:
        next_number = session.execute(
            text("SELECT COALESCE(MAX(version_number), 0) + 1 FROM translation_versions WHERE thread_id = :tid"),
            {"tid": thread_id},
        ).scalar()

        version_row = session.execute(
            text("""
                INSERT INTO translation_versions (thread_id, version_number, translated_text, translator_notes, status)
                VALUES (:tid, :vn, :text, '', 'draft')
                RETURNING id, version_number
            """),
            {"tid": thread_id, "vn": next_number, "text": translated_text},
        ).mappings().first()
        version_id = version_row["id"]

        _persist_stage_runs(session, version_id, stage_records)

    return {"version_id": version_id, "version_number": version_row["version_number"]}


def save_document_revision(thread_id: int, structured_output: dict, stage_records: list[StageRecord]) -> dict:
    """Like save_revision, but for document mode — also stores the
    reassembled structured_output (paragraphs/rows), and flattens it the
    same way save_document_translation does for translated_text."""
    flat_text = (
        "\n\n".join(structured_output.get("paragraphs") or [])
        if structured_output.get("kind") == "prose"
        else "\n".join(",".join(row) for row in (structured_output.get("rows") or []))
    )

    with get_db_session() as session:
        next_number = session.execute(
            text("SELECT COALESCE(MAX(version_number), 0) + 1 FROM translation_versions WHERE thread_id = :tid"),
            {"tid": thread_id},
        ).scalar()

        version_row = session.execute(
            text("""
                INSERT INTO translation_versions
                    (thread_id, version_number, translated_text, translator_notes, status, structured_output)
                VALUES (:tid, :vn, :text, '', 'draft', :structured)
                RETURNING id, version_number
            """),
            {"tid": thread_id, "vn": next_number, "text": flat_text, "structured": _to_jsonb(structured_output)},
        ).mappings().first()
        version_id = version_row["id"]

        _persist_stage_runs(session, version_id, stage_records)

    return {"version_id": version_id, "version_number": version_row["version_number"]}


def save_document_translation(
    user_id: int,
    source_lang: str,
    target_lang: str,
    filename: str,
    original_structured: dict,
    result: DocumentResult,
) -> dict:
    """original_structured / result are JSON-serializable
    ({"kind": "prose", "paragraphs": [...]} or {"kind": "table", "rows": [[...]]})."""
    structured_output = (
        {"kind": "prose", "paragraphs": result.paragraphs, "is_heading": result.is_heading}
        if result.kind == "prose"
        else {"kind": "table", "rows": result.rows}
    )
    flat_text = (
        "\n\n".join(result.paragraphs or [])
        if result.kind == "prose"
        else "\n".join(",".join(row) for row in (result.rows or []))
    )
    flat_source = (
        "\n\n".join(original_structured.get("paragraphs") or [])
        if original_structured.get("kind") == "prose"
        else "\n".join(",".join(row) for row in (original_structured.get("rows") or []))
    )

    with get_db_session() as session:
        thread_row = session.execute(
            text("""
                INSERT INTO translation_threads
                    (user_id, source_lang, target_lang, input_mode, original_source_text,
                     original_structured, original_filename)
                VALUES (:uid, :src, :tgt, 'upload', :text, :structured, :filename)
                RETURNING id
            """),
            {
                "uid": user_id,
                "src": source_lang,
                "tgt": target_lang,
                "text": f"[{filename}]\n{flat_source}",
                "structured": _to_jsonb(original_structured),
                "filename": filename,
            },
        ).mappings().first()
        thread_id = thread_row["id"]

        version_row = session.execute(
            text("""
                INSERT INTO translation_versions
                    (thread_id, version_number, translated_text, translator_notes, status, structured_output)
                VALUES (:tid, 1, :text, '', 'draft', :structured)
                RETURNING id
            """),
            {"tid": thread_id, "text": flat_text, "structured": _to_jsonb(structured_output)},
        ).mappings().first()
        version_id = version_row["id"]

        total_prompt_tokens, total_completion_tokens = _persist_stage_runs(session, version_id, result.stage_records)

    return {
        "thread_id": thread_id,
        "version_id": version_id,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "cache_hits": result.cache_hits,
        "cache_misses": result.cache_misses,
    }


# ---------------------------------------------------------------------------
# Comments (span-annotation editor, CONCEPT.md §3)
# ---------------------------------------------------------------------------

_VALID_CATEGORIES = {"accuracy", "fluency", "terminology", "style"}


def create_comment(
    version_id: int,
    user_id: int,
    user_full_name: str,
    span_start: int,
    span_end: int,
    quoted_text: str,
    comment_text: str,
    category: str,
    chunk_index: int | None = None,
) -> dict:
    if category not in _VALID_CATEGORIES:
        category = "accuracy"
    with get_db_session() as session:
        exists = session.execute(
            text("SELECT id FROM translation_versions WHERE id = :vid"), {"vid": version_id}
        ).scalar()
        if not exists:
            raise ValueError("version not found")

        row = session.execute(
            text("""
                INSERT INTO translation_comments
                    (version_id, span_start, span_end, quoted_text, comment_text, category, created_by, chunk_index)
                VALUES (:vid, :start, :end, :quoted, :comment, :category, :uid, :chunk)
                RETURNING id, version_id, span_start, span_end, quoted_text, comment_text,
                          category, status, created_by, created_at, resolved_at, chunk_index
            """),
            {
                "vid": version_id,
                "start": span_start,
                "end": span_end,
                "quoted": quoted_text,
                "comment": comment_text,
                "category": category,
                "uid": user_id,
                "chunk": chunk_index,
            },
        ).mappings().first()
    return {**dict(row), "created_by_name": user_full_name}


def list_comments(version_id: int) -> list[dict]:
    with get_db_session() as session:
        rows = session.execute(
            text("""
                SELECT c.id, c.version_id, c.span_start, c.span_end, c.quoted_text, c.comment_text,
                       c.category, c.status, c.created_by, u.full_name AS created_by_name,
                       c.created_at, c.resolved_at, c.chunk_index
                FROM translation_comments c
                JOIN users u ON u.id = c.created_by
                WHERE c.version_id = :vid
                ORDER BY c.span_start ASC, c.created_at ASC
            """),
            {"vid": version_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def resolve_comment(comment_id: int) -> dict | None:
    with get_db_session() as session:
        row = session.execute(
            text("""
                UPDATE translation_comments
                SET status = 'resolved', resolved_at = NOW()
                WHERE id = :cid
                RETURNING id, version_id, span_start, span_end, quoted_text, comment_text,
                          category, status, created_by, created_at, resolved_at
            """),
            {"cid": comment_id},
        ).mappings().first()
    return dict(row) if row else None


def delete_comment(comment_id: int, user_id: int) -> bool:
    with get_db_session() as session:
        row = session.execute(
            text("DELETE FROM translation_comments WHERE id = :cid AND created_by = :uid RETURNING id"),
            {"cid": comment_id, "uid": user_id},
        ).scalar()
    return bool(row)


# ---------------------------------------------------------------------------
# Distillation corpus (CONCEPT.md §5/§6) — one row per comment that produced
# a real revision, meant to eventually train an in-house translation model.
# ---------------------------------------------------------------------------


def save_correction_examples(examples: list[dict]) -> None:
    """Each example: thread_id, version_before_id, version_after_id,
    comment_id, chunk_index, source_lang, target_lang, source_text,
    mt_output, corrected_output, quoted_text, comment_text, category,
    created_by, reasoning (the model's own one-sentence explanation for
    the edit, or why it declined to make one — optional, defaults to
    empty). Best-effort — callers should catch and log, not let a
    corpus-logging failure break the actual comment/regenerate response."""
    if not examples:
        return
    with get_db_session() as session:
        for ex in examples:
            session.execute(
                text("""
                    INSERT INTO correction_examples
                        (thread_id, version_before_id, version_after_id, comment_id, chunk_index,
                         source_lang, target_lang, source_text, mt_output, corrected_output,
                         quoted_text, comment_text, category, created_by, reasoning)
                    VALUES
                        (:thread_id, :version_before_id, :version_after_id, :comment_id, :chunk_index,
                         :source_lang, :target_lang, :source_text, :mt_output, :corrected_output,
                         :quoted_text, :comment_text, :category, :created_by, :reasoning)
                """),
                {**ex, "reasoning": ex.get("reasoning") or ""},
            )


def rate_correction_by_comment(comment_id: int, rating: str, user_id: int) -> int:
    """Rates the correction(s) a specific comment produced — the reviewer's
    thumbs sit right on the comment card, not detached from it. A comment
    normally produces exactly one correction_examples row, but could
    produce more than one if it stayed open across several regenerate
    cycles (still-open comments get re-included each time) — rate them
    all the same way rather than pick one arbitrarily. Returns how many
    rows were updated."""
    with get_db_session() as session:
        result = session.execute(
            text("""
                UPDATE correction_examples
                SET rating = :rating, rated_by = :uid, rated_at = NOW()
                WHERE comment_id = :cid
            """),
            {"cid": comment_id, "rating": rating, "uid": user_id},
        )
    return result.rowcount


def list_comments_for_thread(thread_id: int) -> list[dict]:
    """All comments across every version in a thread, each enriched with
    its own resulting correction (if any) so the UI can show the specific
    before/after + rating right on that comment — comments now persist
    across a revision instead of disappearing when the displayed version
    moves forward, enabling multi-turn refinement on the same thread."""
    with get_db_session() as session:
        rows = session.execute(
            text("""
                SELECT c.id, c.version_id, c.span_start, c.span_end, c.quoted_text, c.comment_text,
                       c.category, c.status, c.created_by, u.full_name AS created_by_name,
                       c.created_at, c.resolved_at, c.chunk_index,
                       ce.mt_output, ce.corrected_output, ce.rating, ce.reasoning
                FROM translation_comments c
                JOIN translation_versions v ON v.id = c.version_id
                JOIN users u ON u.id = c.created_by
                LEFT JOIN LATERAL (
                    SELECT mt_output, corrected_output, rating, reasoning
                    FROM correction_examples ce
                    WHERE ce.comment_id = c.id
                    ORDER BY ce.created_at DESC
                    LIMIT 1
                ) ce ON true
                WHERE v.thread_id = :tid
                ORDER BY c.created_at ASC
            """),
            {"tid": thread_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def list_correction_examples() -> list[dict]:
    """The actual export — training-ready rows plus two repeat-signal
    counts (same exact flagged phrase recurring vs. same class of
    feedback recurring, e.g. "too formal" said about different phrases)
    and the reviewer's up/down rating on the revision it came from."""
    with get_db_session() as session:
        rows = session.execute(
            text("""
                SELECT
                    ce.id, ce.thread_id, ce.chunk_index, ce.source_lang, ce.target_lang,
                    ce.source_text, ce.mt_output, ce.corrected_output,
                    ce.quoted_text, ce.comment_text, ce.category, ce.reasoning,
                    ce.created_by, u.full_name AS created_by_name, ce.created_at,
                    ce.rating, ce.rated_at,
                    (SELECT COUNT(*) FROM correction_examples ce2
                     WHERE TRIM(LOWER(ce2.quoted_text)) = TRIM(LOWER(ce.quoted_text))) AS expression_repeat_count,
                    (SELECT COUNT(*) FROM correction_examples ce3
                     WHERE TRIM(LOWER(ce3.comment_text)) = TRIM(LOWER(ce.comment_text))) AS feedback_repeat_count
                FROM correction_examples ce
                LEFT JOIN users u ON u.id = ce.created_by
                ORDER BY ce.created_at DESC
            """)
        ).mappings().all()
    return [dict(r) for r in rows]
