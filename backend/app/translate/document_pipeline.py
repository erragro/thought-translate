"""
app/translate/document_pipeline.py
====================================
Orchestrates translating a parsed document (document_parser.py output)
chunk-by-chunk through the existing single-text pipeline, then
reassembles it into the same shape it came in as (CONCEPT.md §2 —
"a document in -> a document-shaped output").

Caps (MAX_PARAGRAPHS / MAX_TEXT_CELLS) exist because each chunk is a
real Sarvam call — an unbounded document could mean hundreds of calls
in one synchronous request. Simple reject-with-a-clear-message for v1,
not chunked/async processing yet.

Briefly cut much lower (15 / 30) on 2026-08-03 to bound worst-case
revision time, back when revise_translation_multi regenerated a whole
paragraph per call and could burn the account's full 4096-token
reasoning ceiling (~1-2 min per call, sometimes with no output at all).
That problem is fixed now — revise_translation_multi asks for a
targeted per-span replacement instead of a full regeneration, which
measured at 7-17s per call in testing, not minutes. So that specific
justification for a low cap is gone; restored to the original values,
which reflect the actual remaining constraint: bulk upload translation
is still N sequential /translate calls in one synchronous request (fast
per-call, ~1s, but no chunked/async processing yet), so an unbounded
document could still mean an uncomfortably long single request.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from app.translate.document_parser import ProseDocument, TableDocument
from app.translate.pipeline import PipelineResult, StageRecord, revise_translation_multi, run_pipeline
from app.translate.sarvam_client import MAX_TRANSLATE_INPUT_CHARS, get_sarvam_client

MAX_PARAGRAPHS = 100
MAX_TEXT_CELLS = 200

# Table comments are keyed by a single chunk_index int (same column the
# prose path uses for paragraph index), so a cell's (row, col) is encoded
# as row * CELL_CHUNK_COL_MULTIPLIER + col. 100_000 columns of headroom is
# far past any sheet MAX_TEXT_CELLS=200 would actually let through — the
# frontend must encode/decode with this exact same constant.
CELL_CHUNK_COL_MULTIPLIER = 100_000


def encode_cell_chunk_index(row: int, col: int) -> int:
    return row * CELL_CHUNK_COL_MULTIPLIER + col


def decode_cell_chunk_index(chunk_index: int) -> tuple[int, int]:
    return divmod(chunk_index, CELL_CHUNK_COL_MULTIPLIER)


class DocumentTooLarge(Exception):
    pass


@dataclass
class DocumentResult:
    kind: str  # "prose" | "table"
    paragraphs: list[str] | None = None
    is_heading: list[bool] | None = None  # parallel to paragraphs, prose only
    rows: list[list[str]] | None = None
    stage_records: list[StageRecord] = field(default_factory=list)
    cache_hits: int = 0
    cache_misses: int = 0


# Splits English-punctuation sentence boundaries (source text is
# English or Hindi pre-translation, both use these terminators) plus
# Devanagari's danda/double-danda, in case a source paragraph is
# itself already in Hindi.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?॥।])\s+")


def _chunk_text_by_limit(text: str, limit: int) -> list[str]:
    """Paragraph/cell-level chunking (splitting by blank lines, in
    document_parser.py) keeps each Sarvam call to one logical unit, but
    a single paragraph can still come out longer than Sarvam's own
    per-call character cap — a long OCR'd block of continuous text, or
    just a long paragraph in a PDF/DOCX. Split further at sentence
    boundaries where possible, falling back to word then hard-character
    boundaries only if a single "sentence" or word is itself too long.
    Returns [text] unchanged when it's already within the limit, so this
    is a no-op for the common case."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []

    def _pack(pieces: list[str]) -> list[str]:
        packed: list[str] = []
        current = ""
        for piece in pieces:
            candidate = f"{current} {piece}".strip() if current else piece
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                packed.append(current)
            current = piece if len(piece) <= limit else ""
            if not current and piece:
                # A single sentence/word is itself over the limit —
                # hard-slice it as a last resort.
                packed.extend(piece[i : i + limit] for i in range(0, len(piece), limit))
        if current:
            packed.append(current)
        return packed

    sentences = [s for s in _SENTENCE_SPLIT_RE.split(text) if s]
    chunks: list[str] = []
    for sentence in sentences:
        if len(sentence) <= limit:
            chunks.append(sentence)
        else:
            chunks.extend(_pack(sentence.split(" ")))
    return _pack(chunks)


def _is_numeric_or_blank(cell: str) -> bool:
    cell = cell.strip()
    if not cell:
        return True
    try:
        float(cell.replace(",", ""))
        return True
    except ValueError:
        return False


def _run(
    source_lang: str,
    target_lang: str,
    text: str,
    client,
    result: DocumentResult,
    mode: str | None,
    output_script: str | None,
    numerals_format: str,
) -> str:
    """Translates one paragraph/cell. A chunk that's already within
    Sarvam's per-call character cap makes exactly one call, same as
    before — _chunk_text_by_limit returns it unchanged. A chunk over the
    cap (a long OCR'd block, a long PDF/DOCX paragraph) gets split
    further and translated as multiple calls, then stitched back into
    one string, so the paragraph-level chunking document_parser.py
    already does is never the last line of defense against Sarvam's
    hard limit."""
    translated_parts: list[str] = []
    for sub_chunk in _chunk_text_by_limit(text, MAX_TRANSLATE_INPUT_CHARS):
        pr: PipelineResult = run_pipeline(
            source_lang,
            target_lang,
            sub_chunk,
            client=client,
            mode=mode,
            output_script=output_script,
            numerals_format=numerals_format,
        )
        result.stage_records.extend(pr.stage_records)
        if pr.from_cache:
            result.cache_hits += 1
        else:
            result.cache_misses += 1
        translated_parts.append(pr.translated_text)
    return " ".join(translated_parts)


def translate_document(
    source_lang: str,
    target_lang: str,
    doc: ProseDocument | TableDocument,
    mode: str | None = None,
    output_script: str | None = "fully-native",
    numerals_format: str = "international",
) -> DocumentResult:
    client = get_sarvam_client()

    if isinstance(doc, ProseDocument):
        if len(doc.paragraphs) > MAX_PARAGRAPHS:
            raise DocumentTooLarge(
                f"Document has {len(doc.paragraphs)} paragraphs, max supported is {MAX_PARAGRAPHS} for now."
            )
        result = DocumentResult(kind="prose")
        result.paragraphs = [
            _run(source_lang, target_lang, para, client, result, mode, output_script, numerals_format)
            if para.strip()
            else para
            for para in doc.paragraphs
        ]
        result.is_heading = list(doc.is_heading)  # translation doesn't change heading-ness
        return result

    text_cell_count = sum(1 for row in doc.rows for cell in row if not _is_numeric_or_blank(cell))
    if text_cell_count > MAX_TEXT_CELLS:
        raise DocumentTooLarge(
            f"Sheet has {text_cell_count} text cells to translate, max supported is {MAX_TEXT_CELLS} for now."
        )
    result = DocumentResult(kind="table")
    translated_rows: list[list[str]] = []
    for row in doc.rows:
        new_row = [
            cell
            if _is_numeric_or_blank(cell)
            else _run(source_lang, target_lang, cell, client, result, mode, output_script, numerals_format)
            for cell in row
        ]
        translated_rows.append(new_row)
    result.rows = translated_rows
    return result


def regenerate_prose_document(
    source_lang: str,
    target_lang: str,
    original_paragraphs: list[str],
    current_paragraphs: list[str],
    current_is_heading: list[bool],
    open_comments: list[dict],
) -> DocumentResult:
    """Batch regeneration for document mode — one revision call per
    paragraph that has open comments, each combining ALL of that
    paragraph's feedback into a single call (not one call per comment),
    since a document commonly gets several comments before the user
    hits "regenerate". Paragraphs with no open comments are left as-is.
    """
    by_chunk: dict[int, list[dict]] = defaultdict(list)
    for c in open_comments:
        if c.get("chunk_index") is not None:
            by_chunk[c["chunk_index"]].append(c)

    result = DocumentResult(kind="prose")
    new_paragraphs = list(current_paragraphs)
    for idx, chunk_comments in by_chunk.items():
        if idx >= len(original_paragraphs) or idx >= len(current_paragraphs):
            continue
        feedback_items = [
            {
                "quoted_text": c["quoted_text"],
                "comment_text": c["comment_text"],
                "category": c["category"],
                "span_start": c["span_start"],
                "span_end": c["span_end"],
            }
            for c in chunk_comments
        ]
        revised, rec = revise_translation_multi(
            source_lang, target_lang, original_paragraphs[idx], current_paragraphs[idx], feedback_items
        )
        new_paragraphs[idx] = revised
        result.stage_records.append(rec)

    result.paragraphs = new_paragraphs
    result.is_heading = list(current_is_heading)  # unchanged by regeneration
    return result


def regenerate_table_document(
    source_lang: str,
    target_lang: str,
    original_rows: list[list[str]],
    current_rows: list[list[str]],
    open_comments: list[dict],
) -> DocumentResult:
    """Same batch-by-chunk pattern as regenerate_prose_document, keyed by
    cell instead of paragraph — chunk_index decodes to (row, col) via
    decode_cell_chunk_index. Cells with no open comments are left as-is."""
    by_chunk: dict[int, list[dict]] = defaultdict(list)
    for c in open_comments:
        if c.get("chunk_index") is not None:
            by_chunk[c["chunk_index"]].append(c)

    result = DocumentResult(kind="table")
    new_rows = [list(row) for row in current_rows]
    for idx, chunk_comments in by_chunk.items():
        row, col = decode_cell_chunk_index(idx)
        if row >= len(original_rows) or row >= len(current_rows):
            continue
        if col >= len(original_rows[row]) or col >= len(current_rows[row]):
            continue
        feedback_items = [
            {
                "quoted_text": c["quoted_text"],
                "comment_text": c["comment_text"],
                "category": c["category"],
                "span_start": c["span_start"],
                "span_end": c["span_end"],
            }
            for c in chunk_comments
        ]
        revised, rec = revise_translation_multi(
            source_lang, target_lang, original_rows[row][col], current_rows[row][col], feedback_items
        )
        new_rows[row][col] = revised
        result.stage_records.append(rec)

    result.rows = new_rows
    return result
