"""What happens to stored matches when a LISTING is edited.

One rule, applied to both match surfaces: a property (or builder project)
whose details change loses the cached matches that were computed from its old
details — on the client-inquiry side for every client except one who is
already assigned to it or has completed a visit to it, and on the
broker-requirement side for every requirement. Why that is the right thing to
do, and why nothing is re-scored here, is spelled out in
Database/edited_property_match_repository.py, which performs the removal.

This module is the thin layer above it:

  - `edit_affects_matching` decides whether an edit is even capable of
    changing a score, so adding a photo or an Instagram reel link — by far the
    most common edit, and the Landing Page flow's bread and butter — costs
    nothing at all;
  - `handle_listing_edited` routes to the database implementation, or to the
    in-memory caches when DATABASE_URL is unset, the same fallback split every
    other store in this project keeps.

Callers (property_pipeline_service.update_property, builder_project_store.
update) invoke this AFTER the edit has been saved and treat any failure here
as non-fatal — a match row that outlived its edit is a stale row, not a lost
edit, and the nightly pass corrects it either way.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from Database.client_session import is_client_database_configured
from Middleware import step_logger

# Editable fields no score is built from: not in
# embedding_service.EMBEDDING_TEXT_FIELDS (so the vector cannot move) and not
# read anywhere in scoring.py (so no explicit component can move either).
# Everything ELSE is treated as match-relevant, deliberately — an edit wrongly
# believed harmless would leave a stale match standing, while an edit wrongly
# believed harmful only costs one extra delete that the next scoring pass
# undoes.
MATCH_NEUTRAL_FIELDS = frozenset(
    {
        "image_urls",
        "instagram_reel_url",
        "location_url",
        "video_available",
    }
)


def edit_affects_matching(
    content_updates: Optional[Mapping[str, Any]], needs_review: Optional[bool] = None
) -> bool:
    """Whether this edit could change how the listing scores.

    True when it touches any field that is not purely for display, and also
    when it pushes the listing into the review queue — a needs_review listing
    is not matchable at all (matching_service.is_matchable), so its cached
    matches have to go even if not one content field changed with it.
    """
    if needs_review is True:
        return True
    if not content_updates:
        return False
    return any(field not in MATCH_NEUTRAL_FIELDS for field in content_updates)


def handle_listing_edited(record_id: str) -> Dict[str, int]:
    """Drops the stale matches for one edited listing. Returns
    {"client": n, "requirement": n} — how many rows went, for the log line.

    Never raises: callers are save paths, and an edit that succeeded must not
    be reported as a failure because a cache could not be cleaned. A failure
    here leaves the stale rows in place until the next scoring pass, which is
    exactly where they were before this existed.
    """
    try:
        if is_client_database_configured():
            from Database import edited_property_match_repository

            client_rows, requirement_rows = edited_property_match_repository.purge_matches_for_edited_property(
                record_id
            )
        else:
            client_rows, requirement_rows = _purge_in_memory(record_id)
        if client_rows or requirement_rows:
            step_logger.info(
                f"[Matching] {record_id} was edited — dropped {client_rows} client match(es) and "
                f"{requirement_rows} requirement match(es); they are re-scored against the new details "
                "on the next pass (assigned/completed ones were kept)."
            )
        return {"client": client_rows, "requirement": requirement_rows}
    except Exception as exc:  # noqa: BLE001
        step_logger.error(
            f"[Matching] Could not drop the stale matches for edited listing {record_id} ({exc!r}) — the "
            "edit itself is saved, and the next scoring pass corrects them."
        )
        return {"client": 0, "requirement": 0}


def _purge_in_memory(record_id: str) -> tuple:
    """The no-database fallback. Same rule, asked of the in-memory caches:
    every client whose cached scores mention this property loses that score
    unless they are assigned to it or have completed a visit to it, and every
    requirement loses it outright.

    The protection is checked per affected client with the same two public
    lookups the Inquiries table's counts use — a handful of dictionary reads
    here, since this path only ever runs with no database configured.
    """
    from Service.AgentManagementService import agent_store
    from Service.BrokerRequirementService import requirement_match_store
    from Service.ClientPropertyMatchingService import matching_service

    def is_protected(phone: str) -> bool:
        return record_id in agent_store.get_assigned_property_ids(phone) or record_id in (
            agent_store.get_completed_property_ids(phone)
        )

    client_rows = matching_service.drop_edited_property_from_memory_cache(record_id, is_protected)
    requirement_rows = requirement_match_store.drop_property_in_memory(record_id)
    return client_rows, requirement_rows or 0
