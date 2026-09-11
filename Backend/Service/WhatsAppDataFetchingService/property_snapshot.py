"""The in-memory property snapshot — the single copy of the property list
this process works from, and the reason almost nothing in the application
reads the properties table any more.

WHAT IT HOLDS

The newest _SNAPSHOT_LIMIT properties, in the same order every existing
query returns them (by the table's own id), each carrying everything the
application actually needs about a property EXCEPT its photos:

  - the property itself, including its embedding vector, so match scoring
    runs entirely from memory;
  - the photo COUNT (the list views show a count, never the pixels);
  - created_at / updated_at, which is what lets the daily rescore ask
    "what changed since I last looked?" instead of re-scoring everything;
  - the Instagram media id and reel-link time, so the comment/DM poller
    reads its watched reels from here rather than keeping a second,
    parallel cache of the same rows.

Photos are deliberately absent. `image_urls` holds base64 image data —
megabytes per row — and no list, no scoring pass and no detail view needs
it; the one thing that does (someone pressing "Show photos" on one
property) fetches that single property's photos on demand through
property_vector_store.get_property_images.

WHY

Every property read used to be a query. The list pages poll, the matching
pipeline re-read the whole table once per client per run, the Instagram
poller re-read it every few seconds, and each of those reads carried every
property's photo data across the wire whether or not anything had changed.
Against a serverless database billed by compute-time and egress, that is
the dominant cost of running this application, and essentially none of it
was buying anything: the answer was virtually always identical to the last
one.

So the database is read in exactly three situations now:

  1. once, to build this snapshot (and again after a restart);
  2. once per property WRITE, to fold that one row back in with the
     timestamps Postgres actually assigned it;
  3. when someone asks for one property's photos.

CONSISTENCY

This process is the only writer (single uvicorn worker — see main.py's
lifespan), and every write goes through property_vector_store, which
updates this snapshot as part of the same call. There is no polling, no
TTL and no revision counter to get wrong: the snapshot changes when, and
only when, the data changes.

Active in database mode only. With no DATABASE_URL the in-memory fallback
in property_vector_store already IS the whole store, and there would be
nothing for a snapshot to save.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional

from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty

# How many of the most recent properties are held. Everything the UI and
# the matching pipeline read is bounded by this, and it is deliberately far
# above this project's realistic table size (hundreds to low thousands) so
# that in practice the snapshot IS the whole table.
#
# Cost per property is roughly the text fields plus a 384-dimension
# embedding — kilobytes, not megabytes, precisely because photos are
# excluded — so the whole snapshot is a few tens of MB at the limit.
_SNAPSHOT_LIMIT = 5000


@dataclass
class SnapshotEntry:
    """One property as the application sees it. `prop.image_urls` is always
    empty here by construction; `image_count` is the real number."""

    row_id: int
    prop: EmbeddedProperty
    image_count: int
    created_at: datetime
    updated_at: datetime
    media_pk: Optional[str] = None
    reel_linked_at: Optional[datetime] = None


# Oldest-first, matching what property_repository.get_all_properties and
# get_all_properties_summary have always returned, so every caller's
# ordering is unchanged.
_entries: List[SnapshotEntry] = []
_by_id: Dict[str, SnapshotEntry] = {}
_loaded = False
# True when the last load returned a full _SNAPSHOT_LIMIT rows, i.e. the
# table is larger than the snapshot. Only then can removing a property
# reveal an older one that belongs in the window — see note_removed.
_at_capacity = False

# Re-entrant: note_written below both mutates and reads the snapshot, and
# the readers are called from FastAPI request threads, the Instagram poll
# thread and the daily recompute thread at the same time.
_lock = threading.RLock()

# Injected by property_vector_store at import time, rather than imported
# here, purely to keep this module free of any database import — it holds
# and shapes the data, and has no opinion about where it came from.
_load_all: Optional[Callable[[int], List[SnapshotEntry]]] = None
_load_one: Optional[Callable[[str], Optional[SnapshotEntry]]] = None


def configure(
    load_all: Callable[[int], List[SnapshotEntry]],
    load_one: Callable[[str], Optional[SnapshotEntry]],
) -> None:
    global _load_all, _load_one
    _load_all = load_all
    _load_one = load_one


def _ensure_loaded() -> None:
    """Builds the snapshot on first use and after an invalidation. Callers
    hold _lock, so two threads arriving together can never both load."""
    global _loaded, _at_capacity
    if _loaded:
        return
    assert _load_all is not None, "property_snapshot.configure() was never called"
    loaded = _load_all(_SNAPSHOT_LIMIT)
    _entries.clear()
    _entries.extend(loaded)
    _by_id.clear()
    _by_id.update({entry.prop.record_id: entry for entry in loaded})
    _at_capacity = len(loaded) >= _SNAPSHOT_LIMIT
    _loaded = True


def invalidate() -> None:
    """Forces a full rebuild on the next read."""
    global _loaded
    with _lock:
        _loaded = False


def get_window(limit: int) -> List[SnapshotEntry]:
    """The newest `limit` properties, oldest-first — the exact window and
    order `SELECT ... ORDER BY id DESC LIMIT n` followed by a reverse used
    to return."""
    with _lock:
        _ensure_loaded()
        return _entries[-limit:] if limit < len(_entries) else list(_entries)


def get_all() -> List[SnapshotEntry]:
    """Every held property, oldest-first. A copy, so a caller iterating it
    (a scoring pass over thousands of properties) is never disturbed by a
    write landing on another thread mid-loop."""
    with _lock:
        _ensure_loaded()
        return list(_entries)


def get(record_id: str) -> Optional[SnapshotEntry]:
    with _lock:
        _ensure_loaded()
        return _by_id.get(record_id)


def holds_entire_table() -> bool:
    """True when the snapshot is not merely a window but the whole table —
    i.e. the last load came back short of the limit, so no property exists
    outside it.

    Callers use this to decide whether a question about the WHOLE table
    ("how many are there?", "what is the newest change?") can be answered
    from memory or has to go to the database. It is what keeps those
    answers exactly correct at any table size rather than only at this
    project's current one.
    """
    with _lock:
        _ensure_loaded()
        return not _at_capacity


def newest_update() -> Optional[datetime]:
    """The latest updated_at across everything held, or None when empty."""
    with _lock:
        _ensure_loaded()
        return max((entry.updated_at for entry in _entries), default=None)


def count() -> int:
    with _lock:
        _ensure_loaded()
        return len(_entries)


def note_written(record_id: str) -> None:
    """Folds one just-written property back into the snapshot.

    Re-read from the database rather than assembled from what the caller
    wrote, so the snapshot carries the timestamps Postgres actually
    assigned — see property_repository.get_snapshot_row.

    Placement is by row id, which is how the underlying table orders: an
    edit keeps the property exactly where it was in the list, and a new
    property lands wherever its id says it belongs (in practice the end).
    A property older than everything held, while the snapshot is already
    full, is outside the window and is correctly ignored rather than
    appended as if it were the newest thing there is.
    """
    global _at_capacity
    with _lock:
        _ensure_loaded()
        assert _load_one is not None, "property_snapshot.configure() was never called"
        loaded = _load_one(record_id)
        if loaded is None:
            # Written and then deleted before this ran, or never there.
            _drop(record_id)
            return

        existing = _by_id.get(record_id)
        if existing is not None:
            _entries[_entries.index(existing)] = loaded
            _by_id[record_id] = loaded
            return

        if _at_capacity and _entries and loaded.row_id < _entries[0].row_id:
            return  # older than the window this snapshot covers

        position = len(_entries)
        while position > 0 and _entries[position - 1].row_id > loaded.row_id:
            position -= 1
        _entries.insert(position, loaded)
        _by_id[record_id] = loaded
        while len(_entries) > _SNAPSHOT_LIMIT:
            _by_id.pop(_entries.pop(0).prop.record_id, None)
            _at_capacity = True


def note_removed(record_id: str) -> None:
    """Drops a deleted property.

    If the snapshot was full, the deletion frees a slot that an older
    property outside the window now belongs in, and the only way to know
    which is to rebuild — so the next read reloads. Below capacity (this
    project's actual situation) the snapshot already holds the whole table,
    nothing is waiting outside it, and the removal is simply applied.
    """
    global _loaded
    with _lock:
        if _drop(record_id) and _at_capacity:
            _loaded = False


def note_media_pk(record_id: str, media_pk: str) -> None:
    """Records the Instagram media id resolved for a property. A pure
    in-memory update: the database write is the caller's own, and this
    keeps the poller from ever asking for the id again."""
    with _lock:
        entry = _by_id.get(record_id)
        if entry is not None:
            entry.media_pk = media_pk


def _drop(record_id: str) -> bool:
    entry = _by_id.pop(record_id, None)
    if entry is None:
        return False
    try:
        _entries.remove(entry)
    except ValueError:
        pass
    return True
