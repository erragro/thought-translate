"""
app/translate/terminology.py
==============================
Deterministic terminology check for the Validate stage (CONCEPT.md §7 /
"Resolved via best practices" §C).

This starter list is illustrative, not authoritative — CONCEPT.md's
decided sourcing strategy is frequency-based curation from a real
contemporary corpus (AI4Bharat's BPCC/Samanantar), not hand-picked pairs.
Seeded here with a handful of well-known, widely-cited cases so the
Validate stage has something real to check on day one; replace/grow this
with the properly-sourced list once that curation work happens.
"""

from __future__ import annotations

# archaic/over-formal term -> preferred common term
ARCHAIC_TO_COMMON_HI: dict[str, str] = {
    "दूरभाष": "फ़ोन",
    "संगणक": "कंप्यूटर",
    "अंतरजाल": "इंटरनेट",
    "विद्युत डाक": "ईमेल",
}


def find_archaic_terms(text: str) -> list[dict[str, str]]:
    """Returns a list of {"found": archaic_term, "suggested": common_term}
    for every archaic term present in `text`. Pure lookup — no LLM call."""
    hits = []
    for archaic, common in ARCHAIC_TO_COMMON_HI.items():
        if archaic in text:
            hits.append({"found": archaic, "suggested": common})
    return hits
