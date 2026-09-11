"""Storage abstraction plus in-memory cache for sold-out properties — the
one place the sold-out feature reads and writes them:

  - DATABASE_URL unset: a plain in-memory list, the same fallback every
    other store in this project keeps (see property_vector_store.py).
  - DATABASE_URL set: delegates to Database/soldout_property_repository.py,
    and holds the result in memory afterwards.

WHY IT CACHES

The Sold out tab polls like every other page, and the status endpoint —
polled every 7 seconds by every open browser tab, all day (see
Frontend/src/state/StatusProvider.tsx) — carries this list's change token
so those pages only re-fetch when something actually changed. If either of
those questions reached Postgres, this feature alone would keep a
scale-to-zero database permanently awake, which is the exact cost trap
Service/WhatsAppDataFetchingService/property_snapshot.py exists to avoid
for properties.

So the database is read here in exactly three situations:

  1. once, to load the cache (and again after a restart);
  2. once per sale, as part of the move transaction, to fold that one row
     back in with the `sold_out_at` Postgres actually assigned;
  3. when someone asks for one sold-out property's photos.

This is safe for the same reason the property snapshot is: this process is
the only writer (single uvicorn worker, see main.py's lifespan), and the
table is append-only — a sold-out row is never edited and never deleted,
so there is no in-place change for a cache to miss.

WHAT IT HOLDS

Newest sale first, photos excluded, photo COUNT kept. `image_urls` is base64
image data — megabytes per row — and the list, the search and the detail
dialog all show a count, never the pixels; the one thing that does need
them (pressing "Show photos" on one record) fetches that single record's
photos on demand.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Dict, List, Optional, Set

from Database import soldout_property_repository
from Database.session import is_database_configured

# How many sold-out properties are held in memory. Far above any realistic
# number of completed deals for this business (a handful a month), so in
# practice the cache IS the whole table — and each entry is only text fields
# plus a photo count, since neither photos nor the embedding vector are
# carried (see the module docstring and Database/soldout_property_models.py).
_CACHE_LIMIT = 5000


class SoldOutEntry:
    """One sold-out property as the application sees it: the stored column
    values, plus the real photo count. Deliberately not a pydantic model —
    the API shape is built from this by soldout_property_service, which is
    also the only place that knows about display formatting."""

    __slots__ = ("fields", "image_count")

    def __init__(self, fields: dict, image_count: int) -> None:
        self.fields = fields
        self.image_count = image_count

    @property
    def record_id(self) -> str:
        return self.fields["record_id"]

    @property
    def sold_out_at(self) -> datetime:
        return self.fields["sold_out_at"]


# Newest sale first — the order the Sold out tab shows and the order the
# repository's own query returns, so the first entry is always the newest
# there is (which is what makes version() below exact without a query).
_entries: List[SoldOutEntry] = []
_by_id: Dict[str, SoldOutEntry] = {}
_loaded = False
# The real total, which equals len(_entries) unless the table is larger than
# _CACHE_LIMIT. Tracked rather than re-counted: the table is append-only and
# this process is the only writer, so a count taken once at load time stays
# exact as long as every append goes through note_added below.
_total = 0

# Re-entrant: note_added both mutates and reads, and the readers are called
# from FastAPI request threads while a write may be landing on another.
_lock = threading.RLock()


def _ensure_loaded() -> None:
    """Loads the cache on first use. Callers hold _lock, so two threads
    arriving together can never both load."""
    global _loaded, _total
    if _loaded:
        return
    if not is_database_configured():
        # The in-memory fallback's list IS the store — there is nothing to
        # load it from, and nothing to keep in step.
        _loaded = True
        _total = len(_entries)
        return
    rows = soldout_property_repository.get_snapshot_rows(_CACHE_LIMIT)
    _entries.clear()
    _entries.extend(SoldOutEntry(row.fields, row.image_count) for row in rows)
    _by_id.clear()
    _by_id.update({entry.record_id: entry for entry in _entries})
    # A load that came back completely full means the table is bigger than
    # this cache, and only then is one COUNT worth asking for — once per
    # process, never per poll (see the repository's own comment).
    _total = (
        soldout_property_repository.get_sold_out_count() if len(_entries) >= _CACHE_LIMIT else len(_entries)
    )
    _loaded = True


def invalidate() -> None:
    """Forces a full reload on the next read. Not used in normal operation
    (the table is append-only and every append is folded in), kept as the
    deliberate escape hatch if a caller ever has reason to distrust the
    cache."""
    global _loaded
    with _lock:
        _loaded = False


def note_added(fields: dict, image_count: int) -> None:
    """Folds one just-recorded sale into the cache, at the front (newest
    first). The values come from the row as it was read back inside the move
    transaction, so `sold_out_at` is the timestamp Postgres assigned rather
    than one guessed here — the same reasoning property_snapshot.note_written
    documents for a property write."""
    global _total
    with _lock:
        _ensure_loaded()
        entry = SoldOutEntry(fields, image_count)
        existing = _by_id.get(entry.record_id)
        if existing is not None:
            # Can only happen if the same record was somehow recorded twice;
            # the table's unique constraint makes that a rolled-back
            # transaction, so this is belt-and-braces rather than a real path.
            _entries[_entries.index(existing)] = entry
            _by_id[entry.record_id] = entry
            return
        _entries.insert(0, entry)
        _by_id[entry.record_id] = entry
        _total += 1
        while len(_entries) > _CACHE_LIMIT:
            _by_id.pop(_entries.pop().record_id, None)


def get_all(limit: int) -> List[SoldOutEntry]:
    """The `limit` most recent sales, newest first. A copy, so a caller
    iterating it is never disturbed by a sale landing on another thread
    mid-loop."""
    with _lock:
        _ensure_loaded()
        return list(_entries[:limit])


def get(record_id: str) -> Optional[SoldOutEntry]:
    with _lock:
        _ensure_loaded()
        return _by_id.get(record_id)


def count() -> int:
    with _lock:
        _ensure_loaded()
        return _total


def get_sold_out_ids() -> Set[str]:
    """Every held sold-out property id — the in-memory "is this property
    sold out?" set. Read by surfaces that count property ids coming from
    somewhere OTHER than the properties table (see
    Controller/ClientPropertyMatchingController/matching_controller.py's
    website-enquiry count), which is the one place a sold-out id can still
    turn up after the move has removed it from everywhere else."""
    with _lock:
        _ensure_loaded()
        return set(_by_id.keys())


def version() -> str:
    """A single comparable string that changes whenever this list changes —
    carried on the status poll so the Sold out tab re-fetches only when a
    sale has actually been recorded. Callers never parse it, only compare it
    for equality against what they last saw.

    Answered entirely from memory, which is the whole point: this is asked
    every few seconds by every open tab, and a version that had to query
    Postgres to prove itself would cost more than the answer it saves.
    """
    with _lock:
        _ensure_loaded()
        newest = _entries[0].sold_out_at if _entries else None
        return f"{_total}:{newest.isoformat() if newest else '0'}"


def get_images(record_id: str) -> Optional[List[str]]:
    """One sold-out property's photos, on demand. None when it does not
    exist, which callers must not confuse with [] (exists, no photos)."""
    if is_database_configured():
        return soldout_property_repository.get_images(record_id)
    entry = get(record_id)
    return list(entry.fields.get("image_urls") or []) if entry is not None else None


def add_in_memory(fields: dict) -> None:
    """In-memory fallback only (no DATABASE_URL): records a sale, photos and
    all, since there is no database row to read them back from later. The
    database path goes through note_added above instead, with the photos
    left in Postgres."""
    global _total
    with _lock:
        _ensure_loaded()
        entry = SoldOutEntry(fields, len(fields.get("image_urls") or []))
        if entry.record_id in _by_id:
            return
        _entries.insert(0, entry)
        _by_id[entry.record_id] = entry
        _total += 1
        while len(_entries) > _CACHE_LIMIT:
            _by_id.pop(_entries.pop().record_id, None)
