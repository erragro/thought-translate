"""
app/translate/routes.py
=========================
POST /translate/run        — text-in, text-out (paste mode).
POST /translate/run-document — file upload (PDF/Word/Excel/CSV).
Comment routes               — span-annotation editor, CONCEPT.md §3.

Sarvam only, Hindi<->English only, per the 2026-08-03 build decision.
"""

from __future__ import annotations

import logging
import urllib.parse

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, field_validator

from app.auth.service import UserContext, require_permission
from app.translate.document_builder import NoUnicodeFontAvailable, build_csv, build_docx, build_pdf, build_xlsx
from app.translate.document_parser import ProseDocument, UnsupportedFileType, extract
from app.translate.document_pipeline import (
    DocumentTooLarge,
    decode_cell_chunk_index,
    regenerate_prose_document,
    regenerate_table_document,
    translate_document,
)
from app.translate.pipeline import revise_translation, run_pipeline
from app.translate.sarvam_client import MAX_TRANSLATE_INPUT_CHARS
from app.translate.service import (
    create_comment,
    delete_comment,
    get_latest_version,
    get_thread,
    get_version,
    list_comments,
    list_comments_for_thread,
    list_correction_examples,
    list_versions,
    rate_correction_by_comment,
    resolve_comment,
    save_correction_examples,
    save_document_revision,
    save_document_translation,
    save_revision,
    save_translation,
)

logger = logging.getLogger("thought_translate.translate_routes")

router = APIRouter(prefix="/translate", tags=["translate"])

_SUPPORTED_LANGUAGES = {"en", "hi"}

# Sarvam's mayura:v1 model options (confirmed 2026-08-03 via Sarvam's own
# docs) — mode is tone/register, output_script controls roman vs native
# transliteration, numerals_format controls native-digit vs 0-9 digits.
_SUPPORTED_MODES = {"formal", "modern-colloquial", "classic-colloquial", "code-mixed"}
_SUPPORTED_OUTPUT_SCRIPTS = {"roman", "fully-native", "spoken-form-in-native"}
_SUPPORTED_NUMERALS_FORMATS = {"international", "native"}


# Paste mode sends the whole box as one call with no sub-chunking
# (unlike document mode, which splits into paragraphs/cells and, as of
# this fix, further sub-splits any chunk over the limit) — so pasted
# text over Sarvam's cap needs to be rejected with a clear message
# before it ever reaches Sarvam, rather than failing there and
# surfacing as a generic, misleading error.
_MAX_PASTE_CHARS = MAX_TRANSLATE_INPUT_CHARS


def _validate_lang_pair(source_lang: str, target_lang: str) -> None:
    if source_lang not in _SUPPORTED_LANGUAGES or target_lang not in _SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Supported languages: {sorted(_SUPPORTED_LANGUAGES)}")
    if source_lang == target_lang:
        raise HTTPException(status_code=400, detail="Source and target language must differ")


class TranslateRequest(BaseModel):
    source_lang: str
    target_lang: str
    text: str
    input_mode: str = "paste"
    mode: str = "formal"
    output_script: str | None = "fully-native"
    numerals_format: str = "international"

    @field_validator("source_lang", "target_lang")
    @classmethod
    def validate_language(cls, v: str) -> str:
        if v not in _SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported language: {v}. Supported: {sorted(_SUPPORTED_LANGUAGES)}")
        return v

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must not be empty")
        if len(v) > _MAX_PASTE_CHARS:
            raise ValueError(
                f"Text is {len(v)} characters, which is over the {_MAX_PASTE_CHARS}-character limit "
                "for pasted text. Use Upload document for longer text, it's split into chunks "
                "automatically."
            )
        return v

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in _SUPPORTED_MODES:
            raise ValueError(f"Unsupported mode: {v}. Supported: {sorted(_SUPPORTED_MODES)}")
        return v

    @field_validator("output_script")
    @classmethod
    def validate_output_script(cls, v: str | None) -> str | None:
        if v is not None and v not in _SUPPORTED_OUTPUT_SCRIPTS:
            raise ValueError(f"Unsupported output_script: {v}. Supported: {sorted(_SUPPORTED_OUTPUT_SCRIPTS)}")
        return v

    @field_validator("numerals_format")
    @classmethod
    def validate_numerals_format(cls, v: str) -> str:
        if v not in _SUPPORTED_NUMERALS_FORMATS:
            raise ValueError(f"Unsupported numerals_format: {v}. Supported: {sorted(_SUPPORTED_NUMERALS_FORMATS)}")
        return v


@router.post("/run")
def translate_run(
    payload: TranslateRequest,
    user: UserContext = Depends(require_permission("translate", "edit")),
):
    _validate_lang_pair(payload.source_lang, payload.target_lang)

    try:
        result = run_pipeline(
            payload.source_lang,
            payload.target_lang,
            payload.text,
            mode=payload.mode,
            output_script=payload.output_script,
            numerals_format=payload.numerals_format,
        )
    except Exception as exc:
        logger.error("Pipeline failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Translation pipeline failed. Check SARVAM_API_KEY is set.")

    saved = save_translation(
        user_id=user.id,
        source_lang=payload.source_lang,
        target_lang=payload.target_lang,
        input_mode=payload.input_mode,
        source_text=payload.text,
        result=result,
    )

    return {
        "thread_id": saved["thread_id"],
        "version_id": saved["version_id"],
        "translation": result.translated_text,
        "notes": result.translator_notes,
        "from_cache": result.from_cache,
        "token_usage": {
            "prompt_tokens": saved["prompt_tokens"],
            "completion_tokens": saved["completion_tokens"],
            "total_tokens": saved["prompt_tokens"] + saved["completion_tokens"],
        },
        "stages": [
            {
                "stage": rec.stage,
                "output": rec.output_json,
                "prompt_tokens": rec.prompt_tokens,
                "completion_tokens": rec.completion_tokens,
                "latency_ms": rec.latency_ms,
            }
            for rec in result.stage_records
        ],
    }


@router.post("/run-document")
async def translate_run_document(
    source_lang: str = Form(...),
    target_lang: str = Form(...),
    file: UploadFile = File(...),
    mode: str = Form("formal"),
    output_script: str | None = Form("fully-native"),
    numerals_format: str = Form("international"),
    user: UserContext = Depends(require_permission("translate", "edit")),
):
    _validate_lang_pair(source_lang, target_lang)
    if mode not in _SUPPORTED_MODES:
        raise HTTPException(status_code=400, detail=f"Unsupported mode: {mode}. Supported: {sorted(_SUPPORTED_MODES)}")
    if output_script is not None and output_script not in _SUPPORTED_OUTPUT_SCRIPTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported output_script: {output_script}. Supported: {sorted(_SUPPORTED_OUTPUT_SCRIPTS)}",
        )
    if numerals_format not in _SUPPORTED_NUMERALS_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported numerals_format: {numerals_format}. Supported: {sorted(_SUPPORTED_NUMERALS_FORMATS)}",
        )

    data = await file.read()
    try:
        doc = extract(file.filename or "upload", data)
    except UnsupportedFileType as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Document extraction failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=400, detail="Could not read this file. Is it a valid PDF/Word/Excel/CSV?")

    original_structured = (
        {"kind": "prose", "paragraphs": doc.paragraphs, "is_heading": doc.is_heading}
        if isinstance(doc, ProseDocument)
        else {"kind": "table", "rows": doc.rows}
    )

    try:
        result = translate_document(
            source_lang, target_lang, doc, mode=mode, output_script=output_script, numerals_format=numerals_format
        )
    except DocumentTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except Exception as exc:
        logger.error("Document pipeline failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Translation pipeline failed. Check SARVAM_API_KEY is set.")

    saved = save_document_translation(
        user_id=user.id,
        source_lang=source_lang,
        target_lang=target_lang,
        filename=file.filename or "upload",
        original_structured=original_structured,
        result=result,
    )

    return {
        "thread_id": saved["thread_id"],
        "version_id": saved["version_id"],
        "kind": result.kind,
        "paragraphs": result.paragraphs,
        "is_heading": result.is_heading,
        "rows": result.rows,
        "cache_hits": saved["cache_hits"],
        "cache_misses": saved["cache_misses"],
    }


# ---------------------------------------------------------------------------
# Comments — span-annotation editor (CONCEPT.md §3)
# ---------------------------------------------------------------------------


class CommentCreateRequest(BaseModel):
    span_start: int
    span_end: int
    quoted_text: str
    comment_text: str
    category: str = "accuracy"
    chunk_index: int | None = None
    # Paste mode (default): comment immediately triggers a revision.
    # Document mode passes False — comments accumulate, a separate
    # "regenerate" call (below) batches all of them into one pass.
    auto_revise: bool = True

    @field_validator("comment_text")
    @classmethod
    def validate_comment_text(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("comment_text must not be empty")
        return v

    @field_validator("span_end")
    @classmethod
    def validate_span(cls, v: int, info) -> int:
        start = info.data.get("span_start")
        if start is not None and v <= start:
            raise ValueError("span_end must be greater than span_start")
        return v


@router.post("/versions/{version_id}/comments")
def add_comment(
    version_id: int,
    payload: CommentCreateRequest,
    user: UserContext = Depends(require_permission("translate", "edit")),
):
    try:
        comment = create_comment(
            version_id=version_id,
            user_id=user.id,
            user_full_name=user.full_name,
            span_start=payload.span_start,
            span_end=payload.span_end,
            quoted_text=payload.quoted_text,
            comment_text=payload.comment_text,
            category=payload.category,
            chunk_index=payload.chunk_index,
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Translation version not found")

    # Commenting retriggers the translation (CONCEPT.md's revision loop)
    # — degrade gracefully if the revision call fails: the comment is
    # already saved either way, don't lose it over a Sarvam hiccup.
    # Document mode passes auto_revise=False and batches instead (see
    # /regenerate below) — one call per Sarvam-comment would mean many
    # redundant calls across a multi-paragraph document.
    new_version = None
    version = get_version(version_id)
    thread = get_thread(version["thread_id"]) if version else None
    if payload.auto_revise and version and thread:
        try:
            revised_text, stage_record = revise_translation(
                source_lang=thread["source_lang"],
                target_lang=thread["target_lang"],
                source_text=thread["original_source_text"],
                current_translation=version["translated_text"],
                quoted_text=payload.quoted_text,
                comment_text=payload.comment_text,
                category=payload.category,
                span_start=payload.span_start,
                span_end=payload.span_end,
            )
            saved = save_revision(thread["id"], revised_text, [stage_record])
            new_version = {
                "version_id": saved["version_id"],
                "version_number": saved["version_number"],
                "translation": revised_text,
            }
            try:
                save_correction_examples([{
                    "thread_id": thread["id"],
                    "version_before_id": version_id,
                    "version_after_id": saved["version_id"],
                    "comment_id": comment["id"],
                    "chunk_index": payload.chunk_index,
                    "source_lang": thread["source_lang"],
                    "target_lang": thread["target_lang"],
                    "source_text": thread["original_source_text"],
                    "mt_output": version["translated_text"],
                    "corrected_output": revised_text,
                    "quoted_text": payload.quoted_text,
                    "comment_text": payload.comment_text,
                    "category": payload.category,
                    "created_by": user.id,
                }])
            except Exception as exc:
                logger.error("Failed to log correction example: %s", exc, exc_info=True)
        except Exception as exc:
            logger.error("Revision failed after comment: %s", exc, exc_info=True)

    return {"comment": comment, "new_version": new_version}


@router.get("/versions/{version_id}/comments")
def get_comments(
    version_id: int,
    user: UserContext = Depends(require_permission("translate", "view")),
):
    return list_comments(version_id)


@router.get("/threads/{thread_id}/comments")
def get_thread_comments(
    thread_id: int,
    user: UserContext = Depends(require_permission("translate", "view")),
):
    """Every comment across every version in the thread, each carrying its
    own resulting correction (mt_output/corrected_output/rating) if one
    exists — comments persist across a revision instead of disappearing
    when the displayed version moves forward."""
    return list_comments_for_thread(thread_id)


@router.get("/threads/{thread_id}/versions")
def get_versions(
    thread_id: int,
    user: UserContext = Depends(require_permission("translate", "view")),
):
    return list_versions(thread_id)


_CONTENT_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".pdf": "application/pdf",
}


@router.get("/threads/{thread_id}/download")
def download_document(
    thread_id: int,
    user: UserContext = Depends(require_permission("translate", "view")),
):
    """Download the latest translated version in the SAME format the
    document was uploaded as. Paste-mode threads have no original file,
    so there's nothing to reconstruct — 400, not a silent empty file."""
    thread = get_thread(thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    if thread["input_mode"] != "upload" or not thread.get("original_filename"):
        raise HTTPException(status_code=400, detail="This thread has no original file to reconstruct")

    version = get_latest_version(thread_id)
    if not version or not version.get("structured_output"):
        raise HTTPException(status_code=404, detail="No translated version found")

    filename = thread["original_filename"]
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    structured = version["structured_output"]
    paragraphs = structured.get("paragraphs") or []
    is_heading = structured.get("is_heading")

    # Images have no sensible "same format" download (we're not
    # generating a translated photo) — the closest honest artifact is a
    # DOCX of the transcribed+translated text, with the filename made to
    # match rather than silently claiming a .jpg that isn't one.
    if ext in (".jpg", ".jpeg", ".png"):
        ext = ".docx"
        filename = filename.rsplit(".", 1)[0] + ".docx"

    try:
        if ext == ".docx":
            data = build_docx(paragraphs, is_heading)
        elif ext == ".xlsx":
            data = build_xlsx(structured.get("rows") or [])
        elif ext == ".csv":
            data = build_csv(structured.get("rows") or [])
        elif ext == ".pdf":
            data = build_pdf(paragraphs, is_heading)
        else:
            raise HTTPException(status_code=400, detail=f"Can't reconstruct this format: {ext or '(unknown)'}")
    except NoUnicodeFontAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    download_name = f"translated_{filename}"
    quoted = urllib.parse.quote(download_name)
    return Response(
        content=data,
        media_type=_CONTENT_TYPES.get(ext, "application/octet-stream"),
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
    )


@router.post("/versions/{version_id}/regenerate")
def regenerate(
    version_id: int,
    user: UserContext = Depends(require_permission("translate", "edit")),
):
    """Document mode's batch trigger — regenerates every chunk (paragraph
    or, for spreadsheets, cell) that has at least one open comment,
    combining all of that chunk's feedback into a single call each (see
    routes.py's add_comment for why this is separate from paste mode's
    per-comment auto-revise)."""
    version = get_version(version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Translation version not found")
    thread = get_thread(version["thread_id"])
    if not thread:
        raise HTTPException(status_code=404, detail="Translation thread not found")

    structured = version.get("structured_output") or {}
    original = thread.get("original_structured") or {}
    kind = structured.get("kind")
    if kind != original.get("kind"):
        raise HTTPException(status_code=400, detail="Original and current version kind mismatch")

    open_comments = [c for c in list_comments(version_id) if c["status"] == "open"]
    if not open_comments:
        raise HTTPException(status_code=400, detail="No open comments to regenerate from")

    try:
        if kind == "prose":
            current_paragraphs = structured["paragraphs"]
            current_is_heading = structured.get("is_heading") or [False] * len(current_paragraphs)
            result = regenerate_prose_document(
                source_lang=thread["source_lang"],
                target_lang=thread["target_lang"],
                original_paragraphs=original["paragraphs"],
                current_paragraphs=current_paragraphs,
                current_is_heading=current_is_heading,
                open_comments=open_comments,
            )
            new_structured = {"kind": "prose", "paragraphs": result.paragraphs, "is_heading": result.is_heading}
        elif kind == "table":
            result = regenerate_table_document(
                source_lang=thread["source_lang"],
                target_lang=thread["target_lang"],
                original_rows=original["rows"],
                current_rows=structured["rows"],
                open_comments=open_comments,
            )
            new_structured = {"kind": "table", "rows": result.rows}
        else:
            raise HTTPException(status_code=400, detail=f"Unknown document kind: {kind}")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Document regeneration failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Regeneration failed. Check SARVAM_API_KEY is set.")

    saved = save_document_revision(thread["id"], new_structured, result.stage_records)

    revised_chunk_indices: list[int] = []
    try:
        examples = []
        for c in open_comments:
            idx = c.get("chunk_index")
            if idx is None:
                continue
            try:
                if kind == "prose":
                    source_text = original["paragraphs"][idx]
                    mt_output = structured["paragraphs"][idx]
                    corrected_output = result.paragraphs[idx]
                else:
                    row, col = decode_cell_chunk_index(idx)
                    source_text = original["rows"][row][col]
                    mt_output = structured["rows"][row][col]
                    corrected_output = result.rows[row][col]
            except (IndexError, KeyError, TypeError):
                continue
            revised_chunk_indices.append(idx)
            examples.append({
                "thread_id": thread["id"],
                "version_before_id": version_id,
                "version_after_id": saved["version_id"],
                "comment_id": c["id"],
                "chunk_index": idx,
                "source_lang": thread["source_lang"],
                "target_lang": thread["target_lang"],
                "source_text": source_text,
                "mt_output": mt_output,
                "corrected_output": corrected_output,
                "quoted_text": c["quoted_text"],
                "comment_text": c["comment_text"],
                "category": c["category"],
                "created_by": c["created_by"],
            })
        save_correction_examples(examples)
    except Exception as exc:
        logger.error("Failed to log correction examples: %s", exc, exc_info=True)

    return {
        "thread_id": thread["id"],
        "version_id": saved["version_id"],
        "version_number": saved["version_number"],
        "kind": kind,
        "paragraphs": result.paragraphs,
        "is_heading": result.is_heading,
        "rows": result.rows,
        "revised_chunk_indices": sorted(set(revised_chunk_indices)),
    }


class RatingRequest(BaseModel):
    rating: str

    @field_validator("rating")
    @classmethod
    def validate_rating(cls, v: str) -> str:
        if v not in ("up", "down"):
            raise ValueError("rating must be 'up' or 'down'")
        return v


@router.patch("/comments/{comment_id}/rating")
def rate_comment(
    comment_id: int,
    payload: RatingRequest,
    user: UserContext = Depends(require_permission("translate", "edit")),
):
    """Thumbs up/down lives right on the comment that produced a fix —
    up means the fix is good AND resolves the comment (it disappears from
    the open list); down leaves the comment open, inviting another
    comment on the same spot for a multi-turn refinement."""
    updated = rate_correction_by_comment(comment_id, payload.rating, user.id)
    if not updated:
        raise HTTPException(status_code=404, detail="No correction found for this comment")
    comment = resolve_comment(comment_id) if payload.rating == "up" else None
    return {"updated": updated, "comment": comment}


@router.get("/corpus/export")
def export_corpus(
    user: UserContext = Depends(require_permission("admin", "admin")),
):
    """The actual distillation-corpus export — every comment that produced
    a real revision, with repeat-signal counts and the reviewer's rating,
    meant to feed an eventual in-house translation model."""
    return list_correction_examples()


@router.patch("/comments/{comment_id}/resolve")
def resolve_comment_route(
    comment_id: int,
    user: UserContext = Depends(require_permission("translate", "edit")),
):
    comment = resolve_comment(comment_id)
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    return comment


@router.delete("/comments/{comment_id}")
def delete_comment_route(
    comment_id: int,
    user: UserContext = Depends(require_permission("translate", "edit")),
):
    deleted = delete_comment(comment_id, user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Comment not found or not yours")
    return {"status": "deleted"}
