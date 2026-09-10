"""The internal AREA KNOWLEDGE BASE — a growing "which place names belong to
which area" map, learned as a pure BY-PRODUCT of the property pipeline.

WHAT THIS IS NOT: it is not a stage of the pipeline, and it does not decide
anything. The LLM structuring stage (Agent/WhatsAppDataFetchingAgent/
property_structurer.py) still does exactly what it did before — it resolves
every property's locality itself, unchanged and unaware this module exists.
Nothing here feeds back into it (yet). This module only WATCHES the
properties the LLM already produced and writes down what it saw:

    "Vesu" -> ["Vesu", "VIP Road", "University Road", "VNSGU Road", ...]

One area name, every distinct place string that has ever shown up on a
property the LLM filed under that area — roads, landmarks, micro-localities,
society/project names. One physical place legitimately appears under several
names ("University Road" and "VNSGU Road" are the same road); both are kept,
because the point is to recognise whatever a broker actually typed.

WHY IT EXISTS: today every batch pays the LLM to re-derive this same area
knowledge from scratch (PART 3 / STEP A of the prompt). Building a real,
observed, local map of it is the first step towards not paying for that —
but that step is deliberately NOT taken here. This module only accumulates
the data and measures how good it has become, so the decision to actually
use it can be made on evidence rather than hope.

THE MEASUREMENT (what the Temporary page shows):
Every single property that comes out of the LLM visits this module exactly
once — that is the "lookup" count. Inside one visit, each distinct place
string pulled off that property is checked against its area's list:
  - HIT   — that string is already recorded under that area. Nothing to
            write; the knowledge base already knew it.
  - WRITE — it was not there, so it is added (the knowledge base just got
            stronger by one string).
A rising hit rate is the whole signal: it says the knowledge base has seen
enough of the real world to recognise it without asking anyone.

WHERE THE FILE LIVES — and why it is NOT under Backend/:
The knowledge base is a plain, hand-readable, hand-editable Python module
(<project root>/KnowledgeBase/area_knowledge_base.py) holding one dict
literal. It is REWRITTEN AT RUNTIME as new place strings are learned, and
that is precisely why it must live outside Backend/: uvicorn's --reload
watcher (see Backend/main.py) watches the whole Backend/ tree and restarts
the server on ANY *.py write inside it — and only on *.py, which no
--reload-exclude in this uvicorn version reliably prevents. Writing this
file under Backend/ would therefore restart the server in the middle of
nearly every batch, killing the live WhatsApp connections and losing the
properties still being processed. Kept one directory up, the watcher cannot
see it and the pipeline is untouched.

It is never imported as a module either — it is read with ast.literal_eval,
so a hand edit is picked up on the next restart without any import-cache
games, and a corrupted/half-edited file degrades to "start empty" instead of
crashing the server at import time.

SAFETY: every public entry point here is called from the pipeline thread
(Service/WhatsAppDataFetchingService/property_pipeline_service.py) while the
API reads it from FastAPI's thread pool, so all shared state is guarded by
one re-entrant lock. Nothing in here is allowed to raise into the pipeline:
the caller wraps it, and the internals swallow their own I/O errors and fall
back to in-memory-only operation. A knowledge base that fails to save must
never cost a real property.
"""

from __future__ import annotations

import ast
import json
import os
import re
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from Middleware import step_logger
from Model.WhatsAppDataFetchingModel.structured_property import StructuredProperty
from Service.WhatsAppDataFetchingService import area_filter_service

# Backend/Service/WhatsAppDataFetchingService/this_file.py
#   parents[0] = .../Service/WhatsAppDataFetchingService
#   parents[1] = .../Service
#   parents[2] = .../Backend        <- uvicorn's --reload watch root
#   parents[3] = .../<project root> <- outside it, on purpose (see docstring)
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_PROJECT_ROOT = _BACKEND_DIR.parent
KNOWLEDGE_BASE_DIR = _PROJECT_ROOT / "KnowledgeBase"
KNOWLEDGE_BASE_PATH = KNOWLEDGE_BASE_DIR / "area_knowledge_base.py"
_STATS_PATH = KNOWLEDGE_BASE_DIR / "area_knowledge_stats.json"

_VARIABLE_NAME = "AREA_KNOWLEDGE_BASE"

# Where a place string can come from on one property. Kept separate in the
# stats because they behave very differently: "area" is the locality itself
# (almost always a hit once the area is known), "address" is where roads and
# landmarks live (the interesting one), and "society" is a named building
# (nearly unique per listing, so a low hit rate there is expected, not a bug).
SOURCES = ("area", "address", "society")

_EVENT_LIMIT = 80

_lock = threading.RLock()

# area_key -> the area's display name, exactly as it goes into the file.
_area_display: Dict[str, str] = {}
# area_key -> {place_key: place display string}.
_area_places: Dict[str, Dict[str, str]] = {}
# place_key -> area_key of the FIRST area that ever recorded it. Only used to
# notice that the same string is now being claimed by a second area, which is
# worth surfacing (it is usually either a genuinely shared name or a bad LLM
# area call) — it never blocks the write.
_place_owner: Dict[str, str] = {}

_area_stats: Dict[str, Dict[str, Any]] = {}
_events: Deque[Dict[str, Any]] = deque(maxlen=_EVENT_LIMIT)
_stats: Dict[str, Any] = {}
_loaded = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _blank_source_stats() -> Dict[str, Dict[str, int]]:
    return {source: {"lookups": 0, "hits": 0, "writes": 0} for source in SOURCES}


def _blank_status_stats() -> Dict[str, Dict[str, int]]:
    return {status: {"lookups": 0, "hits": 0, "writes": 0} for status in ("accepted", "outsider")}


def _blank_stats() -> Dict[str, Any]:
    return {
        # How many LLM batches have been observed at all.
        "batches_observed": 0,
        # Every property model the LLM produced that reached this module —
        # this is the "how many times did we go to the knowledge base" number.
        "properties_seen": 0,
        # ...of which these had an area to file their places under.
        "properties_recorded": 0,
        # ...and these had none (nothing to attribute; nothing written).
        "properties_skipped_no_area": 0,
        # Properties where EVERY place string was already known (pure hit) vs.
        # properties that taught the knowledge base at least one new string.
        "properties_all_known": 0,
        "properties_with_new_places": 0,
        # Place-string level: one property contributes several lookups.
        "place_lookups": 0,
        "place_hits": 0,
        "place_writes": 0,
        # The same string turning up under a second area.
        "cross_area_collisions": 0,
        "by_source": _blank_source_stats(),
        "by_status": _blank_status_stats(),
        "first_observed_at": None,
        "last_observed_at": None,
        "stats_since": _now_iso(),
    }


# --------------------------------------------------------------- normalising

# One address line is really a list of places ("Anurodh Dwar, Citylight Char
# Rasta | opp. Rahul Raj Mall"). Split on the separators brokers actually
# type. A bare "-" is NOT a separator (it shows up inside real names, e.g.
# "Pal-Adajan"); only a spaced " - " is.
_SEGMENT_SPLIT = re.compile(r"[,;|\n\r•]+|\s+[-–—]\s+")

_TRIM_CHARS = " \t.,:;*-–—()[]{}\"'`&#•"

# Proximity words a broker prefixes a landmark with. "Opp. Rahul Raj Mall"
# and "Rahul Raj Mall" are the same place, and storing both would make the
# knowledge base look like it knew twice as much as it does while never
# hitting. Stripped only when followed by a real word boundary, so "Bhatar
# Road" never loses its "Bh" and "Athwa" never loses its "At".
_NOISE_PREFIXES: Tuple[str, ...] = (
    "in front of",
    "just opposite",
    "just behind",
    "just near",
    "adjacent to",
    "opposite to",
    "opposite",
    "next to",
    "close to",
    "back side of",
    "back side",
    "backside",
    "nearby",
    "near by",
    "beside",
    "besides",
    "behind",
    "opp to",
    "opp",
    "b/h",
    "b.h",
    "bh",
    "b/s",
    "nr",
    "near",
    "above",
    "below",
    "the",
    "at",
    "in",
    "on",
)

# Keys that carry no locality information on their own. Matched on the
# normalised key (letters+digits only), never as a substring — "Black
# Residency" survives, a bare "residency" does not.
_GENERIC_PLACE_KEYS = {
    "road",
    "mainroad",
    "street",
    "lane",
    "gali",
    "circle",
    "chowk",
    "charrasta",
    "chokdi",
    "society",
    "soc",
    "apartment",
    "apartments",
    "flat",
    "bungalow",
    "banglow",
    "villa",
    "tower",
    "towers",
    "residency",
    "heights",
    "complex",
    "plot",
    "shop",
    "office",
    "area",
    "city",
    "town",
    "surat",
    "gujarat",
    "india",
    "na",
    "nil",
    "none",
    "null",
    "nan",
    "unknown",
    "other",
    "others",
    "same",
    "new",
    "old",
    "main",
    "sale",
    "rent",
}


def _key(text: str) -> str:
    """The comparison key for a place or area string: letters and digits
    only, lowercased. Deliberately aggressive so the punctuation and spacing
    a broker happens to use never splits one real place into two entries —
    "V.I.P. Road", "VIP road" and "vip  road" are all one key. The ORIGINAL
    string is what gets stored and displayed; this is only ever the key."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _strip_noise_prefix(text: str) -> str:
    cleaned = text.strip(_TRIM_CHARS)
    changed = True
    while changed and cleaned:
        changed = False
        lowered = cleaned.lower()
        for prefix in _NOISE_PREFIXES:
            if not lowered.startswith(prefix):
                continue
            rest = cleaned[len(prefix) :]
            # Only a real word boundary counts, so "Bhatar" keeps its "Bh".
            if rest[:1] not in ("", " ", ".", ",", ":", "-", "/"):
                continue
            cleaned = rest.strip(_TRIM_CHARS)
            changed = True
            break
    return cleaned


def _clean_place(raw: Optional[str]) -> Optional[Tuple[str, str]]:
    """(display, key) for one candidate place string, or None if it carries
    no locality information worth recording."""
    if not raw:
        return None
    display = _strip_noise_prefix(str(raw))
    display = re.sub(r"\s+", " ", display).strip(_TRIM_CHARS)
    if not display:
        return None
    key = _key(display)
    if len(key) < 3 or key.isdigit() or key in _GENERIC_PLACE_KEYS:
        return None
    # A whole address line pasted as one "place" is not a place. Real ones
    # are short; anything this long is a sentence and would never hit again.
    if len(display) > 60:
        return None
    return display, key


def _candidate_places(prop: StructuredProperty, area_display: str, area_key: str) -> List[Tuple[str, str, str]]:
    """Every distinct (display, key, source) place string this one property
    contributes. Deduplicated within the property, so a property whose
    area_name also appears in its address counts as ONE lookup, not two —
    otherwise the hit rate would quietly inflate itself.

    The area is passed in already canonicalised rather than re-derived from
    prop.area_name here: the two must agree, or a property could be filed
    under one key while its own name was recorded under another. It is also
    the one candidate exempt from the generic-word filter — an area is by
    definition a place, so it always leads its own list ("Vesu": ["Vesu",
    ...]) even when its name would otherwise read as a generic word."""
    candidates: List[Tuple[str, str, str]] = []
    seen: set = set()

    if area_key:
        seen.add(area_key)
        candidates.append((area_display or area_key, area_key, "area"))

    def add(raw: Optional[str], source: str) -> None:
        cleaned = _clean_place(raw)
        if cleaned is None:
            return
        display, key = cleaned
        if key in seen:
            return
        seen.add(key)
        candidates.append((display, key, source))

    for segment in _SEGMENT_SPLIT.split(prop.address or ""):
        add(segment, "address")
    add(prop.society_name, "society")
    return candidates


def _canonical_area(raw: str) -> Tuple[str, str]:
    """(display, key) for the area a property is filed under. When the area
    matches one of the client's selected areas (Settings page), that entry's
    spelling wins — so "vesu", "Vesu" and "VESU" can never become three
    separate areas in the file just because three brokers typed it three
    ways."""
    display = re.sub(r"\s+", " ", str(raw)).strip(_TRIM_CHARS)
    key = _key(display)
    if not key:
        return "", ""
    try:
        for keyword in area_filter_service.get_area_keywords():
            if _key(keyword) == key:
                return keyword.strip(), key
    except Exception:  # noqa: BLE001 - never let a settings read break intake
        pass
    # An area seen before keeps the spelling it was first recorded with.
    return _area_display.get(key, display), key


# ------------------------------------------------------------------ the file


_FILE_HEADER = '''"""INTERNAL AREA KNOWLEDGE BASE — area name -> every place string seen in it.

MACHINE-MAINTAINED. This file is rewritten by
Backend/Service/WhatsAppDataFetchingService/area_knowledge_service.py every
time the property pipeline learns a new place string, so any comment added
inside the dict below will be lost on the next write. Editing the ENTRIES by
hand IS supported and encouraged: fix a wrong entry, delete a junk one, or
add a place you know belongs to an area — the change is picked up on the next
backend restart and treated as knowledge the pipeline already had (it starts
counting as a hit from then on).

Each key is an area/locality. Each value is every distinct place string that
has appeared on a property the LLM filed under that area: the area's own
name, roads, landmarks, micro-localities and society/project names. One real
place often has several names ("University Road" and "VNSGU Road" are the
same road) and every one of them is kept on purpose — the point is to
recognise whatever a broker actually typed.

Comparison is done on a punctuation-and-case-insensitive key, so "VIP Road"
and "V.I.P. road" are one entry; the spelling kept here is the first one seen.

This file lives OUTSIDE Backend/ deliberately: uvicorn --reload restarts the
server on any *.py write under Backend/, which would kill the live WhatsApp
connections mid-batch every time a new place was learned.
"""

from typing import Dict, List

'''


def _render_module(knowledge: Dict[str, List[str]]) -> str:
    if not knowledge:
        return f"{_FILE_HEADER}{_VARIABLE_NAME}: Dict[str, List[str]] = {{}}\n"
    lines = [_FILE_HEADER + f"{_VARIABLE_NAME}: Dict[str, List[str]] = {{"]
    for area in sorted(knowledge, key=lambda name: name.lower()):
        lines.append(f"    {area!r}: [")
        for place in knowledge[area]:
            lines.append(f"        {place!r},")
        lines.append("    ],")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _ordered_places(area_key: str) -> List[str]:
    """The area's own name first (it is the anchor of the list and reads best
    there), everything else alphabetical so a diff of the file is meaningful."""
    ordered = sorted(_area_places.get(area_key, {}).values(), key=lambda place: place.lower())
    own = [place for place in ordered if _key(place) == area_key]
    rest = [place for place in ordered if _key(place) != area_key]
    return own + rest


def _snapshot_knowledge() -> Dict[str, List[str]]:
    return {_area_display.get(area_key, area_key): _ordered_places(area_key) for area_key in _area_places}


def _atomic_write(path: Path, text: str) -> None:
    """Write-then-rename, so a reader (or a crash) can never see a half
    written knowledge base — it either has the old file or the new one."""
    KNOWLEDGE_BASE_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def _persist_knowledge() -> None:
    try:
        _atomic_write(KNOWLEDGE_BASE_PATH, _render_module(_snapshot_knowledge()))
    except Exception as exc:  # noqa: BLE001
        # In-memory knowledge is still correct and still measured; only the
        # save failed. Never worth costing the batch that triggered it.
        step_logger.error(f"Could not write the area knowledge base file ({KNOWLEDGE_BASE_PATH}): {exc!r}")


def _persist_stats() -> None:
    payload = {"stats": _stats, "area_stats": _area_stats, "events": list(_events)}
    try:
        _atomic_write(_STATS_PATH, json.dumps(payload, indent=2, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Could not write the area knowledge stats file ({_STATS_PATH}): {exc!r}")


def _parse_knowledge_file(source: str) -> Dict[str, List[str]]:
    """Pulls the dict literal out of the file WITHOUT importing it — a hand
    edit that broke the syntax, or a file half-written by an older crash, must
    degrade to "no knowledge yet", never take the server down at import time."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if value is None:
            continue
        if not any(isinstance(target, ast.Name) and target.id == _VARIABLE_NAME for target in targets):
            continue
        raw = ast.literal_eval(value)
        if not isinstance(raw, dict):
            raise ValueError(f"{_VARIABLE_NAME} is not a dict")
        parsed: Dict[str, List[str]] = {}
        for area, places in raw.items():
            if not isinstance(area, str) or not isinstance(places, (list, tuple)):
                continue
            parsed[area] = [place for place in places if isinstance(place, str)]
        return parsed
    return {}


# ------------------------------------------------------------------ lifecycle


def load_from_disk() -> None:
    """Called once at startup (Backend/main.py). Restores the knowledge base
    and the running analysis, and creates the file if it does not exist yet so
    it is there to open and read from the very first run. Never raises."""
    global _loaded
    with _lock:
        _area_display.clear()
        _area_places.clear()
        _place_owner.clear()
        _area_stats.clear()
        _events.clear()
        _stats.clear()
        _stats.update(_blank_stats())

        knowledge: Dict[str, List[str]] = {}
        if KNOWLEDGE_BASE_PATH.exists():
            try:
                knowledge = _parse_knowledge_file(KNOWLEDGE_BASE_PATH.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                step_logger.error(
                    f"The area knowledge base file ({KNOWLEDGE_BASE_PATH}) could not be read: {exc!r}. "
                    "Starting from an empty knowledge base — the existing file is left untouched until the "
                    "next successful write, so nothing is lost that a fix to the file cannot recover."
                )
                knowledge = {}

        for area, places in knowledge.items():
            area_display, area_key = _canonical_area(area)
            if not area_key:
                continue
            _area_display.setdefault(area_key, area_display)
            bucket = _area_places.setdefault(area_key, {})
            for place in places:
                cleaned = _clean_place(place)
                if cleaned is None:
                    continue
                place_display, place_key = cleaned
                bucket.setdefault(place_key, place_display)
                _place_owner.setdefault(place_key, area_key)

        if _STATS_PATH.exists():
            try:
                _restore_stats(json.loads(_STATS_PATH.read_text(encoding="utf-8")))
            except Exception as exc:  # noqa: BLE001
                step_logger.warn(
                    f"Could not read the area knowledge stats file ({_STATS_PATH}): {exc!r}. Counters start fresh."
                )

        _loaded = True
        if not KNOWLEDGE_BASE_PATH.exists():
            _persist_knowledge()

        step_logger.info(
            f"Area knowledge base loaded: {len(_area_places)} area(s), "
            f"{sum(len(places) for places in _area_places.values())} known place string(s) "
            f"({KNOWLEDGE_BASE_PATH})."
        )


def _restore_stats(payload: Any) -> None:
    """Merges a stats file onto a blank set of counters, so a file written by
    an older build (missing a counter that exists now) restores what it does
    have instead of failing the whole load."""
    if not isinstance(payload, dict):
        return
    stored_stats = payload.get("stats")
    if isinstance(stored_stats, dict):
        merged = _blank_stats()
        merged.update(stored_stats)
        for bucket_name, blank in (("by_source", _blank_source_stats()), ("by_status", _blank_status_stats())):
            current = merged.get(bucket_name)
            if not isinstance(current, dict):
                merged[bucket_name] = blank
            else:
                for name, counters in blank.items():
                    if not isinstance(current.get(name), dict):
                        current[name] = counters
        _stats.clear()
        _stats.update(merged)
    stored_area_stats = payload.get("area_stats")
    if isinstance(stored_area_stats, dict):
        for key, value in stored_area_stats.items():
            if isinstance(value, dict):
                _area_stats[key] = value
    stored_events = payload.get("events")
    if isinstance(stored_events, list):
        for event in stored_events[-_EVENT_LIMIT:]:
            if isinstance(event, dict):
                _events.append(event)


# ----------------------------------------------------------------- observing


def _area_bucket(area_key: str) -> Dict[str, Any]:
    bucket = _area_stats.get(area_key)
    if bucket is None:
        bucket = {"lookups": 0, "hits": 0, "writes": 0, "properties": 0, "first_seen": _now_iso(), "last_updated": None}
        _area_stats[area_key] = bucket
    return bucket


def _observe_one(prop: StructuredProperty) -> Dict[str, Any]:
    """One property's visit to the knowledge base. Returns the event record.
    The caller holds the lock."""
    now = _now_iso()
    _stats["properties_seen"] += 1
    if _stats["first_observed_at"] is None:
        _stats["first_observed_at"] = now
    _stats["last_observed_at"] = now

    status = prop.review_status if prop.review_status in _stats["by_status"] else "accepted"

    event: Dict[str, Any] = {
        "at": now,
        "area": None,
        "source_message_id": prop.source_message_id,
        "record_id": prop.record_id,
        "review_status": prop.review_status,
        "lookups": 0,
        "hits": 0,
        "writes": 0,
        "hit_places": [],
        "new_places": [],
        "collisions": [],
        "skipped": False,
        "skip_reason": None,
    }

    raw_area = (prop.area_name or "").strip()
    area_display, area_key = _canonical_area(raw_area) if raw_area else ("", "")
    if not area_key:
        # Nothing to file the places under. Counted, never guessed at — a
        # property with no locality is exactly the case the knowledge base is
        # eventually meant to fix, so it must stay visible in the numbers.
        _stats["properties_skipped_no_area"] += 1
        event["skipped"] = True
        event["skip_reason"] = (
            "The LLM returned no area_name for this property, so there is nothing to file its places under."
        )
        return event

    _stats["properties_recorded"] += 1
    if area_key not in _area_display:
        _area_display[area_key] = area_display
    elif area_display and area_display != _area_display[area_key]:
        # A later, better spelling (e.g. the client's own Settings casing)
        # wins — same area, same key, just displayed consistently.
        _area_display[area_key] = area_display
    places = _area_places.setdefault(area_key, {})
    bucket = _area_bucket(area_key)
    bucket["properties"] += 1
    bucket["last_updated"] = now
    event["area"] = _area_display[area_key]

    for place_display, place_key, source in _candidate_places(prop, _area_display[area_key], area_key):
        _stats["place_lookups"] += 1
        _stats["by_source"][source]["lookups"] += 1
        _stats["by_status"][status]["lookups"] += 1
        bucket["lookups"] += 1
        event["lookups"] += 1

        if place_key in places:
            _stats["place_hits"] += 1
            _stats["by_source"][source]["hits"] += 1
            _stats["by_status"][status]["hits"] += 1
            bucket["hits"] += 1
            event["hits"] += 1
            event["hit_places"].append(places[place_key])
            continue

        places[place_key] = place_display
        _stats["place_writes"] += 1
        _stats["by_source"][source]["writes"] += 1
        _stats["by_status"][status]["writes"] += 1
        bucket["writes"] += 1
        event["writes"] += 1
        event["new_places"].append(place_display)

        owner = _place_owner.get(place_key)
        if owner is None:
            _place_owner[place_key] = area_key
        elif owner != area_key:
            # Recorded, not blocked: the same name really can exist in two
            # localities, and when it cannot, this is the fastest way to see
            # that one of the two area calls was wrong.
            _stats["cross_area_collisions"] += 1
            event["collisions"].append(f"{place_display} (already known under {_area_display.get(owner, owner)})")

    if event["writes"]:
        _stats["properties_with_new_places"] += 1
    elif event["lookups"]:
        _stats["properties_all_known"] += 1
    return event


def observe_properties(properties: List[StructuredProperty]) -> None:
    """THE ONLY ENTRY POINT THE PIPELINE USES. Records every property the LLM
    just produced into the knowledge base and updates the analysis.

    Purely observational: it reads the property models and writes to its own
    file. It does not modify the properties, does not touch the LLM, does not
    change any verdict, and cannot influence what the rest of the pipeline
    does with them. The caller wraps this in its own try/except as well, so a
    failure here is invisible to the batch."""
    if not properties:
        return
    with _lock:
        if not _loaded:
            # Startup normally loads this; if it somehow did not, load now
            # rather than starting a fresh file over the top of a real one.
            load_from_disk()
        _stats["batches_observed"] += 1
        wrote_anything = False
        for prop in properties:
            event = _observe_one(prop)
            _events.append(event)
            if event["writes"]:
                wrote_anything = True
        if wrote_anything:
            _persist_knowledge()
        _persist_stats()
        step_logger.info(
            f"Area knowledge base: {len(properties)} propert{'y' if len(properties) == 1 else 'ies'} checked — "
            f"lifetime {_stats['place_hits']} hit(s) / {_stats['place_writes']} write(s) across "
            f"{len(_area_places)} area(s)."
        )


# ------------------------------------------------------------------- reading


def _rate(hits: int, lookups: int) -> float:
    return round((hits / lookups) * 100, 1) if lookups else 0.0


def get_overview() -> Dict[str, Any]:
    """Everything the Temporary page shows, in one snapshot."""
    with _lock:
        areas = []
        for area_key, places in _area_places.items():
            bucket = _area_stats.get(area_key, {})
            lookups = int(bucket.get("lookups", 0))
            hits = int(bucket.get("hits", 0))
            areas.append(
                {
                    "area": _area_display.get(area_key, area_key),
                    "place_count": len(places),
                    "places": _ordered_places(area_key),
                    "lookups": lookups,
                    "hits": hits,
                    "writes": int(bucket.get("writes", 0)),
                    "hit_rate": _rate(hits, lookups),
                    "properties": int(bucket.get("properties", 0)),
                    "first_seen": bucket.get("first_seen"),
                    "last_updated": bucket.get("last_updated"),
                }
            )
        areas.sort(key=lambda row: (-row["place_count"], row["area"].lower()))

        by_source = [
            {
                "name": source,
                "lookups": _stats["by_source"][source]["lookups"],
                "hits": _stats["by_source"][source]["hits"],
                "writes": _stats["by_source"][source]["writes"],
                "hit_rate": _rate(_stats["by_source"][source]["hits"], _stats["by_source"][source]["lookups"]),
            }
            for source in SOURCES
        ]
        by_status = [
            {
                "name": status,
                "lookups": counters["lookups"],
                "hits": counters["hits"],
                "writes": counters["writes"],
                "hit_rate": _rate(counters["hits"], counters["lookups"]),
            }
            for status, counters in _stats["by_status"].items()
        ]

        return {
            "file_path": str(KNOWLEDGE_BASE_PATH),
            "totals": {
                "batches_observed": _stats["batches_observed"],
                "properties_seen": _stats["properties_seen"],
                "properties_recorded": _stats["properties_recorded"],
                "properties_skipped_no_area": _stats["properties_skipped_no_area"],
                "properties_all_known": _stats["properties_all_known"],
                "properties_with_new_places": _stats["properties_with_new_places"],
                "place_lookups": _stats["place_lookups"],
                "place_hits": _stats["place_hits"],
                "place_writes": _stats["place_writes"],
                "cross_area_collisions": _stats["cross_area_collisions"],
                "hit_rate": _rate(_stats["place_hits"], _stats["place_lookups"]),
                "area_count": len(_area_places),
                "place_count": sum(len(places) for places in _area_places.values()),
                "first_observed_at": _stats["first_observed_at"],
                "last_observed_at": _stats["last_observed_at"],
                "stats_since": _stats["stats_since"],
            },
            "by_source": by_source,
            "by_status": by_status,
            "areas": areas,
            "events": list(reversed(_events)),
        }


def reset_stats() -> Dict[str, Any]:
    """Zeroes the analysis WITHOUT touching the knowledge base itself — the
    learned place strings stay, so the next run measures the hit rate of the
    knowledge as it stands today. That is the measurement worth having: a
    reset that also wiped the file would only ever re-measure "everything is
    new"."""
    with _lock:
        _stats.clear()
        _stats.update(_blank_stats())
        for bucket in _area_stats.values():
            bucket["lookups"] = 0
            bucket["hits"] = 0
            bucket["writes"] = 0
            bucket["properties"] = 0
        _events.clear()
        _persist_stats()
        step_logger.info("Area knowledge base analysis reset — the learned place strings themselves were kept.")
        return get_overview()
