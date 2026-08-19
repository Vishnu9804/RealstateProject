"""In-memory area-keyword filter — Stage 1 of the property pipeline
("Condition-Based Filter" in the architecture diagram). A message only
qualifies for the rest of the pipeline if its text mentions at least one
configured area keyword, matched as a whole word/phrase and
case-insensitively ("Udhna", "udhna", "UDHNA" are treated as identical).

No DB yet by design (see project instructions) — the keyword list lives in
memory for now. Every caller goes through the functions below rather than
touching `_area_keywords` directly, so swapping this for a settings table
later only touches this file.
"""

from __future__ import annotations

import re
from typing import List

_area_keywords: List[str] = []


def set_area_keywords(keywords: List[str]) -> None:
    """Replaces the entire keyword list. De-duplicates case-insensitively
    and drops blanks, but keeps each keyword's original casing for display
    (matching itself is always case-insensitive regardless)."""
    global _area_keywords
    seen = set()
    deduped: List[str] = []
    for keyword in keywords:
        cleaned = keyword.strip()
        if not cleaned:
            continue
        normalized = cleaned.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(cleaned)
    _area_keywords = deduped


def get_area_keywords() -> List[str]:
    return list(_area_keywords)


def is_qualified(text: str) -> bool:
    """True if `text` mentions at least one configured area keyword as a
    whole word/phrase, case-insensitively. No keywords configured means
    nothing qualifies — an unset filter must never be treated as
    "allow everything through"."""
    if not _area_keywords or not text:
        return False
    return any(_mentions_keyword(text, keyword) for keyword in _area_keywords)


def _mentions_keyword(text: str, keyword: str) -> bool:
    pattern = r"\b" + re.escape(keyword) + r"\b"
    return re.search(pattern, text, re.IGNORECASE) is not None
