"""Area-keyword filter — Stage 1 of the property pipeline ("Condition-Based
Filter" in the architecture diagram). A message only qualifies for the rest
of the pipeline if its text mentions at least one configured area keyword,
matched as a whole word/phrase and case-insensitively ("Udhna", "udhna",
"UDHNA" are treated as identical).

The keyword list lives in memory — `is_qualified` runs on every single
incoming WhatsApp message, so it must never wait on a database round-trip.
When DATABASE_URL is set, `set_area_keywords` also writes through to the
database so the list survives a restart, and `load_from_database` (called
once at startup — see main.py) restores it; the in-memory copy stays the
single source of truth the hot path reads from either way.
"""

from __future__ import annotations

import re
from typing import List

from Database import settings_repository
from Database.session import is_database_configured

_SETTINGS_KEY = "area_filter_keywords"

_area_keywords: List[str] = []


def load_from_database() -> None:
    """Restores the last-saved keyword list on startup. No-op if no
    database is configured, or nothing has been saved yet."""
    if not is_database_configured():
        return
    stored = settings_repository.get_value(_SETTINGS_KEY)
    if stored is not None:
        global _area_keywords
        _area_keywords = list(stored)


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
    if is_database_configured():
        settings_repository.set_value(_SETTINGS_KEY, _area_keywords)


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
