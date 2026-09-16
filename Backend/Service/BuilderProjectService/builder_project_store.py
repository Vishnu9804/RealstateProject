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

WHY IT ALSO FEEDS MATCHING

Builder projects are matched against client inquiries and broker
requirements alongside properties, and — exactly like the property snapshot
(Service/WhatsAppDataFetchingService/property_snapshot.py) — they are
scored straight from this cache: each entry holds the project's match
vector and hands the matching layer a ready-made candidate
(get_match_candidates), so a full rescore costs no database traffic at all.
Up to CACHE_LIMIT projects are held, the same ceiling the property side
scores under.

The vector is a float32 numpy array — about 1.5 KB per project instead of
the ~12 KB the same 384 numbers take as a Python list — and it is computed
only when a save changes the words it is built from (see _embedding_text):
an edit that only touches photos, availability or notes never re-runs the
model.

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

import numpy as np

from Database import builder_project_repository
from Database.builder_project_repository import EDITABLE_CONTENT_FIELDS
from Database.session import is_database_configured
from Middleware import step_logger
from Model.BuilderProjectModel.builder_project import BuilderProject
from Model.BuilderProjectModel.builder_project_candidate import BuilderProjectCandidate
from Service.WhatsAppDataFetchingService import embedding_service

# How many projects are held in memory — and therefore the most that can be
# matched against a client or a requirement. The same ceiling the property
# side holds and scores under (property_snapshot._SNAPSHOT_LIMIT,
# matching_service's own limit), and far above any realistic number of
# hand-entered builder projects, so in practice the cache IS the whole table
# — each entry being only text fields, a photo count and a compact vector.
CACHE_LIMIT = 5000
_CACHE_LIMIT = CACHE_LIMIT


def _as_vector(value: Any) -> Optional[np.ndarray]:
    """A stored or freshly computed vector as a compact float32 array, or
    None when there is none."""
    if value is None:
        return None
    vector = np.asarray(value, dtype=np.float32)
    return vector if vector.size else None


def _embedding_text(fields: Dict[str, Any]) -> str:
    return embedding_service.build_embedding_text_from_fields(fields)


def _embed_text(text: str, record_id: str) -> Optional[List[float]]:
    """The model call itself, never allowed to fail a save or a match: a
    project without a vector is still matched on every other field, and it
    is simply tried again the next time one is needed."""
    try:
        return embedding_service.embed_text(text)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(
            f"[Builder Projects] Could not compute the match vector for {record_id!r} "
            f"(it is still matched on price, area, BHK and type): {exc!r}"
        )
        return None


class BuilderProjectEntry:
    """One project as the application sees it: the stored column values plus
    the real photo count and its match vector. Deliberately not a pydantic
    model — the API shape is built from this by builder_project_service,
    which is also the only place that knows about display formatting.

    `embedding` is kept OUT of `fields`, so no API record can ever carry it."""

    __slots__ = ("fields", "image_count", "embedding", "embedding_failed", "_candidate")

    def __init__(self, fields: dict, image_count: int, embedding: Any = None) -> None:
        self.fields = fields
        self.image_count = image_count
        self.embedding: Optional[np.ndarray] = _as_vector(embedding)
        # Set when computing the vector failed in this process, so a broken
        # model is not retried on every single match pass (see
        # _ensure_embeddings). Cleared by construction on the next write,
        # which builds a fresh entry.
        self.embedding_failed = False
        self._candidate: Optional[BuilderProjectCandidate] = None

    @property
    def record_id(self) -> str:
        return self.fields["record_id"]

    @property
    def updated_at(self) -> Optional[datetime]:
        return self.fields.get("updated_at")

    def candidate(self) -> BuilderProjectCandidate:
        """This project in the shape the matching engine scores. Built once
        per entry and reused — every write replaces the entry, so a cached
        candidate can never outlive the data it was built from. The identity
        check covers the one in-place change an entry sees (its vector being
        filled in), even if another thread built a candidate meanwhile."""
        cached = self._candidate
        if cached is None or cached.embedding is not self.embedding:
            cached = BuilderProjectCandidate.from_fields(self.fields, self.embedding)
            self._candidate = cached
        return cached


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
    _entries.extend(BuilderProjectEntry(row.fields, row.image_count, row.embedding) for row in rows)
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


# --- matching -----------------------------------------------------------------


def get_match_candidates(limit: int = CACHE_LIMIT, ensure_embeddings: bool = True) -> List[BuilderProjectCandidate]:
    """Every held project (up to `limit`, newest first) as a match candidate
    — served from memory, no query. With `ensure_embeddings`, any project
    that has no vector yet gets one first (see _ensure_embeddings), which is
    what every SCORING caller wants; a caller that only needs the projects'
    display fields passes False and never touches the model."""
    entries = get_all(limit)
    if ensure_embeddings:
        _ensure_embeddings(entries)
    return [entry.candidate() for entry in entries]


def get_match_candidates_changed_since(since: Optional[datetime], limit: int = CACHE_LIMIT) -> List[BuilderProjectCandidate]:
    """Projects added or edited strictly after `since` (all of them when
    `since` is None), as match candidates — the builder-project half of
    "what does the incremental rescore need to look at?". Compared on
    updated_at, so an edit counts as well as an addition, exactly like
    property_vector_store.get_properties_changed_since."""
    entries = get_all(limit)
    if since is not None:
        cutoff = _aware(since)
        entries = [entry for entry in entries if entry.updated_at is None or _aware(entry.updated_at) > cutoff]
    if entries:
        _ensure_embeddings(entries)
    return [entry.candidate() for entry in entries]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _ensure_embeddings(entries: List[BuilderProjectEntry]) -> None:
    """Gives every project in `entries` that has no vector yet one — the
    one-time fill for projects saved before builder projects were matched,
    and the retry for any whose vector could not be computed at save time.

    A no-op scan (no model, no query) once every project has its vector,
    which is every call after the first. When there is work, it happens
    under the write lock, so it can never interleave with a save of the same
    project, and the new vectors are stored in ONE transaction that leaves
    each project's updated_at untouched (see
    builder_project_repository.set_embeddings) — so filling them in is not
    mistaken for an edit anywhere. The vectors are used in memory at once
    even if that write fails; the next restart then simply fills them
    again."""
    if not any(entry.embedding is None and not entry.embedding_failed for entry in entries):
        return
    with _write_lock:
        with _lock:
            current = [_by_id.get(entry.record_id) for entry in entries]
        pending = [
            entry for entry in current if entry is not None and entry.embedding is None and not entry.embedding_failed
        ]
        vectors: Dict[str, List[float]] = {}
        for entry in pending:
            vector = _embed_text(_embedding_text(entry.fields), entry.record_id)
            if vector is None:
                entry.embedding_failed = True
            else:
                vectors[entry.record_id] = vector
        if not vectors:
            return
        if is_database_configured():
            try:
                builder_project_repository.set_embeddings(vectors)
            except Exception as exc:  # noqa: BLE001
                step_logger.error(
                    f"[Builder Projects] Could not store {len(vectors)} match vector(s) (they are used "
                    f"from memory until the next restart, which computes them again): {exc!r}"
                )
        for entry in pending:
            vector = vectors.get(entry.record_id)
            if vector is not None:
                entry.embedding = _as_vector(vector)
                entry._candidate = None
        step_logger.info(f"[Builder Projects] Computed the match vector for {len(vectors)} builder project(s).")


# --- writes -------------------------------------------------------------------


def add(project: BuilderProject) -> BuilderProjectEntry:
    with _write_lock:
        with _lock:
            _ensure_loaded()
        fields_for_text = project.model_dump(exclude={"image_urls"})
        vector = _embed_text(_embedding_text(fields_for_text), project.record_id)
        if is_database_configured():
            row = builder_project_repository.add_project(project, vector)
            entry = BuilderProjectEntry(row.fields, row.image_count, row.embedding)
        else:
            now = datetime.now(timezone.utc)
            fields = project.model_dump()
            fields["created_at"] = now
            fields["updated_at"] = now
            entry = BuilderProjectEntry(fields, len(project.image_urls), vector)
        with _lock:
            _fold_in(entry, is_new=True)
        return entry


def update(record_id: str, content_updates: Dict[str, Any]) -> Optional[BuilderProjectEntry]:
    """Applies the editable fields in `content_updates`. None when no
    project with this record_id exists.

    The match vector is recomputed only when the words it is built from
    actually change (or the project has none yet); otherwise the stored one
    is kept as it is. When the words changed but the model call failed, the
    old vector is cleared rather than left describing what the project used
    to say — the next match pass then computes it again."""
    updates = {key: value for key, value in content_updates.items() if key in EDITABLE_CONTENT_FIELDS}
    with _write_lock:
        with _lock:
            _ensure_loaded()
            existing = _by_id.get(record_id)
        embedding_change: Dict[str, Any] = {}
        new_vector: Optional[List[float]] = None
        if existing is not None:
            old_text = _embedding_text(existing.fields)
            new_text = _embedding_text({**existing.fields, **updates})
            if new_text != old_text or existing.embedding is None:
                new_vector = _embed_text(new_text, record_id)
                if new_vector is not None or new_text != old_text:
                    embedding_change["embedding"] = new_vector
        elif any(name in embedding_service.EMBEDDING_TEXT_FIELDS for name in updates):
            # Held nowhere in memory (older than the cache window), so the
            # old words can't be compared — a vector that may no longer
            # describe the project is cleared instead of trusted.
            embedding_change["embedding"] = None

        if is_database_configured():
            row = builder_project_repository.update_project(record_id, updates, **embedding_change)
            if row is None:
                with _lock:
                    _remove(record_id)
                return None
            entry = BuilderProjectEntry(row.fields, row.image_count, row.embedding)
        else:
            if existing is None:
                return None
            fields = {**existing.fields, **updates, "updated_at": datetime.now(timezone.utc)}
            vector = embedding_change["embedding"] if "embedding" in embedding_change else existing.embedding
            entry = BuilderProjectEntry(fields, len(fields.get("image_urls") or []), vector)
        with _lock:
            _fold_in(entry, is_new=False)
        # A builder project is a match candidate exactly like a property (see
        # Service/ClientPropertyMatchingService/match_candidates.py), so an
        # edit to it invalidates the same cached matches, under the same rule
        # and with the same self-healing rescore — see match_invalidation_
        # service.py. Lazy import: the matching feature is not otherwise a
        # dependency of this store.
        from Service.ClientPropertyMatchingService import match_invalidation_service

        if match_invalidation_service.edit_affects_matching(updates):
            match_invalidation_service.handle_listing_edited(record_id)
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
