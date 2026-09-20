"""The Builder Projects page's backend: projects a person adds, edits and
deletes by hand — never captured from WhatsApp, never published to the
landing site. Storage and caching live in builder_project_store.py; this
module shapes the API records.

Builder projects ARE matched against client inquiries and broker
requirements, alongside properties — but that happens entirely on the
matching side (Service/ClientPropertyMatchingService/match_candidates.py
reads them from builder_project_store), so nothing here touches the property
table, the property snapshot or the WhatsApp intake, and this feature can
never change how any of those behave.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from Database.builder_project_repository import EDITABLE_CONTENT_FIELDS
from Database.session import is_database_configured
from Model.BuilderProjectModel.builder_project import BuilderProject, BuilderProjectRecord
from Service.BuilderProjectService import builder_project_store
from Service.BuilderProjectService.builder_project_store import BuilderProjectEntry
from Service.WhatsAppDataFetchingService import display_settings_service, timestamp_formatting

# Columns that are NOT NULL in the table — an explicit null in a PATCH means
# "leave it", never "clear it" (the dialog never sends one; this is what
# keeps a hand-written request from failing on a constraint instead).
# Fields whose model type is not Optional, so an explicit null in a PATCH
# body must be ignored rather than written. contact_phones is one of them:
# the column is NOT NULL and the model declares a plain list (the
# controller's bridge_contact_phones already turns a null into [], so this
# is the second line of defence, not the first).
_NON_NULLABLE_FIELDS = ("listing_type", "image_urls", "contact_phones")


def get_builder_projects(limit: int = 500) -> List[BuilderProjectRecord]:
    """The page's list, newest first, served entirely from memory.
    Photo-less by construction (`image_urls` is always [], `image_count`
    carries the real number)."""
    use_24_hour_format = display_settings_service.get_use_24_hour_format()
    return [_to_record(entry, use_24_hour_format) for entry in builder_project_store.get_all(limit)]


def create_builder_project(content_fields: Dict[str, Any]) -> BuilderProjectRecord:
    fields = {key: value for key, value in content_fields.items() if key in EDITABLE_CONTENT_FIELDS}
    entry = builder_project_store.add(BuilderProject(**fields))
    return _to_record(entry, display_settings_service.get_use_24_hour_format())


def update_builder_project(record_id: str, content_updates: Dict[str, Any]) -> Optional[BuilderProjectRecord]:
    """Applies only the fields present in `content_updates` (the controller
    passes exactly what the request carried). None when no project with this
    record_id exists."""
    updates = {
        key: value
        for key, value in content_updates.items()
        if key in EDITABLE_CONTENT_FIELDS and not (key in _NON_NULLABLE_FIELDS and value is None)
    }
    entry = builder_project_store.update(record_id, updates)
    return _to_record(entry, display_settings_service.get_use_24_hour_format()) if entry is not None else None


def get_builder_project(record_id: str) -> Optional[BuilderProjectRecord]:
    """One project, from memory — what the match dialogs' read-only view
    opens with for a builder project that was assigned to an agent or already
    visited. None when no such project exists (deleted since)."""
    entry = builder_project_store.get(record_id)
    return _to_record(entry, display_settings_service.get_use_24_hour_format()) if entry is not None else None


def delete_builder_project(record_id: str) -> bool:
    """With a database, the stored client and broker-requirement matches
    pointing at this project are deleted in the same transaction as the
    project itself (see builder_project_repository.delete_project). Without
    one, the matching layer's in-memory caches are cleaned here instead — the
    same split the sold-out move uses for a property."""
    deleted = builder_project_store.delete(record_id)
    if deleted and not is_database_configured():
        # Lazy imports: the matching services read builder projects through
        # builder_project_store, so importing them at module load would tie
        # the two features together in both directions.
        from Service.BrokerRequirementService import requirement_matching_service
        from Service.ClientPropertyMatchingService import matching_service

        matching_service.drop_property_from_memory_cache(record_id)
        requirement_matching_service.drop_property_from_memory_cache(record_id)
    return deleted


def get_builder_project_images(record_id: str) -> Optional[List[str]]:
    return builder_project_store.get_images(record_id)


def get_builder_project_count() -> int:
    return builder_project_store.count()


def get_builder_projects_version() -> str:
    """The list's change token. Carries the 12h/24h display setting too,
    because every record's formatted_timestamp depends on it — toggling it
    in Settings must count as a change, or an open page (and the browser's
    cached copy of the list) would keep showing the old format."""
    use_24_hour_format = display_settings_service.get_use_24_hour_format()
    return f"{builder_project_store.version()}:{'24h' if use_24_hour_format else '12h'}"


def get_builder_project_version(record_id: str) -> Optional[str]:
    """One project's change marker — its own updated_at — for the photo
    endpoint's ETag. None for a project the cache doesn't hold, which the
    caller must treat as "do not cache" (see Middleware/http_cache.py)."""
    entry = builder_project_store.get(record_id)
    if entry is None or entry.updated_at is None:
        return None
    return entry.updated_at.isoformat()


def _to_record(entry: BuilderProjectEntry, use_24_hour_format: bool) -> BuilderProjectRecord:
    """`image_urls` is forced to [] whatever `fields` holds: the database path
    never loads that column, and the in-memory fallback does hold it — sending
    it either way would put megabytes of base64 into a list response. The
    match vector lives on the entry, never in `fields`, so it cannot reach an
    API record at all."""
    data = {key: value for key, value in entry.fields.items() if key != "image_urls"}
    created_at = entry.fields.get("created_at")
    return BuilderProjectRecord(
        **data,
        image_urls=[],
        image_count=entry.image_count,
        formatted_timestamp=(
            timestamp_formatting.format_ist(created_at, use_24_hour_format) if created_at is not None else "—"
        ),
    )
