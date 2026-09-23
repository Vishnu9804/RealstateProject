"""What happens to stored matches when a LISTING is edited.

One rule, applied to both match surfaces: a property (or builder project)
whose details change loses the cached matches that were computed from its old
details — on the client-inquiry side for every client except one who is
already assigned to it or has completed a visit to it, and on the
broker-requirement side for every requirement. Why that is the right thing to
do, and why nothing is re-scored here, is spelled out in
Database/edited_property_match_repository.py, which performs the removal.

This module is the thin layer above it:

  - `changed_fields` reduces an Edit dialog's payload to the values that
    actually MOVED. Both dialogs post their whole form on every Save, so
    without this step the question below is asked of "every field the form
    has" and can only ever answer yes;
  - `edit_affects_matching` decides whether what moved is even capable of
    changing a score, so adding a photo or an Instagram reel link — by far the
    most common edit, and the Landing Page flow's bread and butter — costs
    nothing at all;
  - `handle_listing_edited` routes to the database implementation, or to the
    in-memory caches when DATABASE_URL is unset, the same fallback split every
    other store in this project keeps.

The two steps belong together and neither is safe alone: the neutral list is
a claim about which FIELDS cannot move a score, and it means nothing until
something establishes which fields moved at all.

Callers (property_pipeline_service.update_property, builder_project_store.
update) invoke this AFTER the edit has been saved and treat any failure here
as non-fatal — a match row that outlived its edit is a stale row, not a lost
edit, and the nightly pass corrects it either way.
"""

from __future__ import annotations

from collections import abc
from typing import Any, Dict, Mapping, Optional

from Database.client_session import is_client_database_configured
from Middleware import step_logger

# Editable fields an edit to which must NOT cost a listing its place in
# anyone's matches — the photos, the unit/flat number, the contact number,
# the video flag, the Instagram reel and the map link. These are what staff
# correct most often, long after a property has been matched and shared, and
# pulling the property out of every client's and broker's shortlist to
# re-earn its place overnight is the wrong trade for any of them.
#
# NONE of them is read by scoring.py and none is in embedding_service.
# EMBEDDING_TEXT_FIELDS either, so no score CAN move for any of them. The
# contact number used to be the one exception — it was part of the embedding
# text, so an edit to it shifted the semantic half of the score by a hair —
# until identifiers were taken out of that text altogether (see
# EMBEDDING_TEXT_FIELDS for the measurement that prompted that). This list is
# now exactly what it claims to be, with no exception to keep in mind.
#
# Everything ELSE is treated as match-relevant, deliberately — an edit
# wrongly believed harmless would leave a stale match standing, while an
# edit wrongly believed harmful only costs one extra delete that the next
# scoring pass undoes.
MATCH_NEUTRAL_FIELDS = frozenset(
    {
        "image_urls",
        "instagram_reel_url",
        "location_url",
        "video_available",
        "unit_no",
        # The client's own spreadsheet "Extra" column
        # (StructuredProperty.extra_notes) — human-only free text that the
        # LLM never writes and that NOTHING in matching reads: it is not in
        # embedding_service.EMBEDDING_TEXT_FIELDS and not named anywhere in
        # scoring.py, so no score can move for it. It is the listing side's
        # exact counterpart of ClientRecord.notes and
        # StructuredRequirement.notes, both of which are already neutral on
        # their own side — a staff scratchpad must never cost a listing its
        # place in anyone's shortlist.
        "extra_notes",
        # The client's "AVL or Not" toggle (StructuredProperty.is_available,
        # and the identical column on a builder project). It says whether a
        # listing is off the market FOR NOW, and it has no bearing on
        # matching whatsoever: nothing in scoring.py reads it, it is not in
        # embedding_service.EMBEDDING_TEXT_FIELDS, and matching_service.
        # is_matchable does not consult it — an unavailable listing is
        # scored, ranked and shown exactly like any other. So flipping it
        # must not cost the listing its place in a single shortlist; the
        # matches dialogs simply MARK the card instead (MatchedProperty
        # carries the live value, see matching_service._display_fields).
        #
        # Deliberately NOT the same thing as the Sold out tab, which removes
        # a closed deal from the property table entirely and does take its
        # match rows with it (Database/soldout_property_repository.py).
        "is_available",
        # The list is what an edit moves. The old scalar is not named here
        # any more: it is not a field, not a column and not in any request
        # body by the time this runs (the controller's
        # bridge_contact_phones folds a stale browser's copy into the list
        # and removes the key).
        "contact_phones",
    }
)


def changed_fields(existing: Any, updates: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """The subset of `updates` whose value actually DIFFERS from what the
    listing already holds. `existing` may be a model (attributes) or a plain
    mapping (a builder project's `fields`).

    This exists because the Properties and Builder Projects Edit dialogs post
    the WHOLE form on every save, not just the boxes that were touched (see
    PropertyFormDialog.tsx's toPayload). Deciding on the KEYS that arrived
    therefore meant every edit looked like a price change, and the neutral
    list above — the entire point of which is that adding a photo costs
    nothing — could never fire once. Deciding on the VALUES that moved is
    what makes that list mean what it says.

    `existing=None` (a listing held nowhere we can read it from) returns
    `updates` untouched: unable to tell what moved, we keep the old, strictly
    safer answer of "assume everything did".

    Note `image_urls` can read as changed even when it is not, because the
    copies this is given are deliberately photo-less (property_snapshot, and
    builder_project_repository._READ_COLUMNS). It is match-neutral either
    way, so that never reaches a decision.
    """
    if not updates or existing is None:
        return dict(updates) if updates else updates
    getter = existing.get if isinstance(existing, abc.Mapping) else lambda name: getattr(existing, name, None)
    return {field: value for field, value in updates.items() if getter(field) != value}


def edit_affects_matching(
    content_updates: Optional[Mapping[str, Any]], needs_review: Optional[bool] = None
) -> bool:
    """Whether this edit could change how the listing scores.

    True when it CHANGED any field that is not purely for display, and also
    when it pushes the listing into the review queue — a needs_review listing
    is not matchable at all (matching_service.is_matchable), so its cached
    matches have to go even if not one content field changed with it.

    Callers pass the fields that actually moved (see changed_fields above),
    never the raw dialog payload.
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
