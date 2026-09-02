"""Stage 1 of the property pipeline ("Condition-Based Filter" in the
architecture diagram). Its ONLY job is to weed out messages that are
obviously not about a property at all (greetings, festival wishes, forwards,
general chit-chat) before they reach the expensive LLM stage. It must NOT
try to judge whether a message is about one of the client's selected areas
— that used to be done here with a local gazetteer of area aliases
(Data/surat_area_groups.py, now removed), and it did not work: a broker
typing "Savani School" or "VIP Road" instead of "Vesu" would silently fail
the match and the whole property would be lost. No hand-maintained alias
list can ever be complete.

Area relevance is now judged by the LLM itself, per-property, as part of
structuring (see Agent/WhatsAppDataFetchingAgent/property_structurer.py) —
it is given the client's selected areas and Surat-area knowledge no static
keyword list can match, and a property it decides is outside every
selected area is still stored, just flagged review_status="outsider"
(Model/WhatsAppDataFetchingModel/structured_property.py) instead of being
silently dropped.

So this stage is deliberately loose, OR-matched, case-insensitive, and
never area-specific: qualify a message if it merely LOOKS like it could be
about a property — a BHK/RK mention, a carpet-area unit, a plot-size unit
("vaar"), common listing vocabulary (price/location words), or one of the
client's selected areas (still a strong property signal, just not the only
one). The client has already told us the monitored groups are almost
entirely property messages, so false positives here are cheap (the LLM
stage is robust enough to reject a stray greeting that slips through) while
a false negative permanently loses a real listing — so this filter must
never be tightened past "mentions at least one property-ish word".

The keyword list configured on the Settings page (client-selected areas,
e.g. "Vesu", "Althan", "Bamroli") still lives here and is still one of the
OR-matched signals, and is still what gets handed to the LLM as the
client's tracked areas — that part of this module is unchanged. What is
gone is using it as a hard area-match gate.

Performance: both the generic property-word pattern and the area-keyword
pattern are precompiled — the generic one once at import time (it never
changes), the area one only when the keyword list changes (`set_area_keywords`
/ `load_from_database`) — so `is_qualified`, which runs on every single
incoming WhatsApp message, does a fixed number of single-pass regex
searches no matter how many keywords are configured.

The keyword list lives in memory so the hot path never waits on a database
round-trip. When DATABASE_URL is set, `set_area_keywords` also writes
through to the database so the list survives a restart, and
`load_from_database` (called once at startup — see main.py) restores it;
the in-memory copy stays the single source of truth the hot path reads from
either way.
"""

from __future__ import annotations

import re
from typing import List, Optional, Pattern

from Database import settings_repository
from Database.session import is_database_configured

_SETTINGS_KEY = "area_filter_keywords"

# Deliberately generic, deliberately loose — every entry is a signal that a
# message MIGHT be a property listing, not proof that it is (the LLM stage
# makes that call for real). OR-matched, case-insensitive, so any single hit
# qualifies the message. Grouped by what real Surat broker messages look
# like in practice:
_PROPERTY_SIGNAL_PATTERNS: List[str] = [
    # Bedroom configuration — covers flats/bungalows ("2 BHK") and studio
    # units ("1 RK"), which BHK alone would miss.
    r"bhk",
    r"\brk\b",
    # Carpet/plot area units, every common spelling a broker actually types.
    r"sq\.?\s*ft\.?",
    r"square\s*feet",
    r"\bsqft\b",
    r"\bsq\b",
    r"sq\.?\s*yards?\b",
    r"sq\.?\s*mtr?s?\b",
    # "vaar"/"var" — the Gujarati unit land/plot listings are priced in.
    r"\bvaar\b",
    r"\bvar\b",
    # Generic listing vocabulary — price and location language that shows
    # up in almost every real listing regardless of property type.
    r"\bprice\b",
    r"\brent\b",
    r"\bsale\b",
    r"\bresale\b",
    r"\blakh\b",
    r"\blac\b",
    r"\bcrore\b",
    r"\bcr\b",
    r"₹",
    r"\brs\.?\b",
    r"\bdeposit\b",
    r"\bbrokerage\b",
    r"\btoken\b",
    r"\bpossession\b",
    r"\blocation\b",
    r"\baddress\b",
    r"\bsociety\b",
    r"\broad\b",
    r"\bnear\b",
    r"\bopp\.?\b",
    r"\barea\b",
    # Common property-type nouns.
    r"\bplot\b",
    r"\bflat\b",
    r"\bbungalow\b",
    r"\bbanglow\b",
    r"\bvilla\b",
    r"\bapartment\b",
    r"\bshop\b",
    r"\boffice\b",
    r"\bwarehouse\b",
    r"\bland\b",
]

_GENERIC_PATTERN: Pattern[str] = re.compile("|".join(_PROPERTY_SIGNAL_PATTERNS), re.IGNORECASE)

_area_keywords: List[str] = []
_area_match_pattern: Optional[Pattern[str]] = None


def load_from_database() -> None:
    """Restores the last-saved keyword list on startup. No-op if no
    database is configured, or nothing has been saved yet."""
    if not is_database_configured():
        return
    stored = settings_repository.get_value(_SETTINGS_KEY)
    if stored is not None:
        global _area_keywords
        _area_keywords = list(stored)
        _rebuild_area_pattern()


def set_area_keywords(keywords: List[str]) -> None:
    """Replaces the entire client-selected-area list (shown/edited on the
    Settings page). De-duplicates case-insensitively and drops blanks, but
    keeps each keyword's original casing for display (matching itself is
    always case-insensitive regardless). This list feeds two things: it's
    one of the OR-matched signals in `is_qualified`, and it's the set of
    tracked areas handed to the LLM for per-property area matching (see
    property_structurer.py) — it is no longer used for any local alias/
    gazetteer lookup."""
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
    _rebuild_area_pattern()
    if is_database_configured():
        settings_repository.set_value(_SETTINGS_KEY, _area_keywords)


def get_area_keywords() -> List[str]:
    return list(_area_keywords)


def is_qualified(text: str) -> bool:
    """True if `text` looks like it could be a property message at all —
    ANY generic property-word hit, or a mention of one of the client's
    selected areas, qualifies it (see module docstring for why this is
    intentionally loose and not an area-relevance check)."""
    if not text:
        return False
    if _GENERIC_PATTERN.search(text) is not None:
        return True
    return _area_match_pattern is not None and _area_match_pattern.search(text) is not None


def _rebuild_area_pattern() -> None:
    """Recompiles the area-keyword pattern `is_qualified` OR-matches
    against, alongside the generic property-word pattern. Runs only when
    the keyword list changes, never per-message."""
    global _area_match_pattern
    if not _area_keywords:
        _area_match_pattern = None
        return
    alternation = "|".join(re.escape(keyword.strip()) for keyword in _area_keywords if keyword.strip())
    _area_match_pattern = re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)
