"""Storage abstraction plus in-memory cache for builder projects — the one
place the Builder Projects feature reads and writes them:

  - DATABASE_URL unset: a plain in-memory list, the same fallback every
    other store in this project keeps (see soldout_property_store.py).
  - DATABASE_URL set: delegates to Database/builder_project_repository.py,
    and holds the result in memory afterwards.

WHY IT CACHES

The status endpoint — polled every few seconds by every open browser tab,
all day (see Frontend/src/state/StatusProvider.tsx) — carries this list's
change token, and the list endpoint answers conditional requests from it.
If either question reached Postgres, this feature alone would keep a
scale-to-zero database permanently awake. So the database is read here in
exactly three situations:

  1. once, to load the cache (and again after a restart);
  2. once per write, inside that write's own transaction, to fold the one
     row back in with the timestamps Postgres actually assigned;
  3. when someone asks for one project's photos.

This is safe for the same reason the property snapshot is: this process is
the only writer (single uvicorn worker, see main.py's lifespan), and every
write goes through this module.

WHY WRITES ARE SERIALIZED

Writes take `_write_lock` for the whole database round trip, so the order
they are folded into the cache is always the order they committed in — two
near-simultaneous edits of the same project can never leave the older one
in memory. Readers never take that lock: they only ever wait on `_lock`,
which guards pure in-memory work and is never held across a network call
after the first load.

WHAT IT HOLDS

Newest first, photos excluded, photo COUNT kept (the in-memory fallback
keeps the photos in `fields`, since there is nowhere else for them to live).
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from Database import builder_project_repository
from Database.builder_project_repository import EDITABLE_CONTENT_FIELDS
from Database.session import is_database_configured
from Model.BuilderProjectModel.builder_project import BuilderProject

# How many projects are held in memory. Far above any realistic number of
# hand-entered builder projects, so in practice the cache IS the whole table
# — and each entry is only text fields plus a photo count.
_CACHE_LIMIT = 5000


class BuilderProjectEntry:
    """One project as the application sees it: the stored column values plus
    the real photo count. Deliberately not a pydantic model — the API shape
    is built from this by builder_project_service, which is also the only
    place that knows about display formatting."""

    __slots__ = ("fields", "image_count")

    def __init__(self, fields: dict, image_count: int) -> None:
        self.fields = fields
        self.image_count = image_count

    @property
    def record_id(self) -> str:
        return self.fields["record_id"]

    @property
    def updated_at(self) -> Optional[datetime]:
        return self.fields.get("updated_at")


# Newest first — the order the repository's own query returns.
_entries: List[BuilderProjectEntry] = []
_by_id: Dict[str, BuilderProjectEntry] = {}
_loaded = False
# The real total, which equals len(_entries) unless the table is larger than
# _CACHE_LIMIT. Tracked rather than re-counted: this process is the only
# writer, so a count taken once at load time stays exact as long as every
# add/delete goes through this module.
_total = 0

# Re-entrant: the write paths both mutate and read under it.
_lock = threading.RLock()
_write_lock = threading.Lock()


def _ensure_loaded() -> None:
    """Loads the cache on first use. Callers hold _lock, so two threads
    arriving together can never both load."""
    global _loaded, _total
    if _loaded:
        return
    if not is_database_configured():
        # The in-memory fallback's list IS the store — there is nothing to
        # load it from.
        _loaded = True
        _total = len(_entries)
        return
    rows = builder_project_repository.get_snapshot_rows(_CACHE_LIMIT)
    _entries.clear()
    _entries.extend(BuilderProjectEntry(row.fields, row.image_count) for row in rows)
    _by_id.clear()
    _by_id.update({entry.record_id: entry for entry in _entries})
    # Only a load that came back completely full can be hiding older rows,
    # and only then is one COUNT worth asking for — once, never per poll.
    _total = (
        builder_project_repository.get_project_count() if len(_entries) >= _CACHE_LIMIT else len(_entries)
    )
    _loaded = True


def invalidate() -> None:
    """Forces a full reload on the next read — the escape hatch used when a
    delete frees a slot that an older project outside the window now
    belongs in."""
    global _loaded
    with _lock:
        _loaded = False


def get_all(limit: int) -> List[BuilderProjectEntry]:
    """The `limit` newest projects, newest first. A copy, so a caller
    iterating it is never disturbed by a write landing mid-loop."""
    with _lock:
        _ensure_loaded()
        return list(_entries[:limit])


def get(record_id: str) -> Optional[BuilderProjectEntry]:
    with _lock:
        _ensure_loaded()
        return _by_id.get(record_id)


def count() -> int:
    with _lock:
        _ensure_loaded()
        return _total


def version() -> str:
    """A single comparable string that changes whenever this list changes —
    the total plus the newest `updated_at`. An add changes both, an edit
    moves the newest timestamp, a delete changes the total. Answered
    entirely from memory: it is asked every few seconds by every open tab."""
    with _lock:
        _ensure_loaded()
        newest = max((entry.updated_at for entry in _entries if entry.updated_at is not None), default=None)
        return f"{_total}:{newest.isoformat() if newest else '0'}"


def add(project: BuilderProject) -> BuilderProjectEntry:
    with _write_lock:
        with _lock:
            _ensure_loaded()
        if is_database_configured():
            row = builder_project_repository.add_project(project)
            entry = BuilderProjectEntry(row.fields, row.image_count)
        else:
            now = datetime.now(timezone.utc)
            fields = project.model_dump()
            fields["created_at"] = now
            fields["updated_at"] = now
            entry = BuilderProjectEntry(fields, len(project.image_urls))
        with _lock:
            _fold_in(entry, is_new=True)
        return entry


def update(record_id: str, content_updates: Dict[str, Any]) -> Optional[BuilderProjectEntry]:
    """Applies the editable fields in `content_updates`. None when no
    project with this record_id exists."""
    updates = {key: value for key, value in content_updates.items() if key in EDITABLE_CONTENT_FIELDS}
    with _write_lock:
        with _lock:
            _ensure_loaded()
            existing = _by_id.get(record_id)
        if is_database_configured():
            row = builder_project_repository.update_project(record_id, updates)
            if row is None:
                with _lock:
                    _remove(record_id)
                return None
            entry = BuilderProjectEntry(row.fields, row.image_count)
        else:
            if existing is None:
                return None
            fields = {**existing.fields, **updates, "updated_at": datetime.now(timezone.utc)}
            entry = BuilderProjectEntry(fields, len(fields.get("image_urls") or []))
        with _lock:
            _fold_in(entry, is_new=False)
        return entry


def delete(record_id: str) -> bool:
    with _write_lock:
        with _lock:
            _ensure_loaded()
            held = record_id in _by_id
        deleted = builder_project_repository.delete_project(record_id) if is_database_configured() else held
        if deleted:
            with _lock:
                _remove(record_id)
        return deleted


def get_images(record_id: str) -> Optional[List[str]]:
    """One project's photos, on demand. None when it does not exist, which
    callers must not confuse with [] (exists, no photos)."""
    if is_database_configured():
        return builder_project_repository.get_images(record_id)
    entry = get(record_id)
    return list(entry.fields.get("image_urls") or []) if entry is not None else None


def _fold_in(entry: BuilderProjectEntry, is_new: bool) -> None:
    """Places one just-written project into the cache. Callers hold _lock.

    A cache that was invalidated in between is simply left to reload — the
    next read fetches the table exactly as it now is, this write included."""
    global _total
    if not _loaded:
        return
    existing = _by_id.get(entry.record_id)
    if existing is not None:
        _entries[_entries.index(existing)] = entry
        _by_id[entry.record_id] = entry
        return
    if not is_new:
        # An edit to a project older than the window this cache holds —
        # nothing in memory to replace.
        return
    _entries.insert(0, entry)
    _by_id[entry.record_id] = entry
    _total += 1
    while len(_entries) > _CACHE_LIMIT:
        _by_id.pop(_entries.pop().record_id, None)


def _remove(record_id: str) -> None:
    """Drops a deleted project. Callers hold _lock. When the table is bigger
    than the cache, the freed slot belongs to an older project only a reload
    can name — so the next read reloads."""
    global _total, _loaded
    if not _loaded:
        return
    entry = _by_id.pop(record_id, None)
    if entry is None:
        return
    _entries.remove(entry)
    _total = max(0, _total - 1)
    if _total > len(_entries):
        _loaded = False
