"""Owns the "back half" of the property pipeline: receiving flushed message
batches from the buffering stage (Service/WhatsAppDataFetchingService/message_buffer_service.py),
running them through the LLM structuring stage (Agent/
property_structurer.py), the embedding stage (Service/WhatsAppDataFetchingService/embedding_service.py),
and the duplicate-detection stage (Service/WhatsAppDataFetchingService/duplicate_detection_service.py),
then storing the result (Service/WhatsAppDataFetchingService/property_vector_store.py) for the
Controller layer to read.

Every structured property is embedded exactly once, right here, right after
structuring. Those vectors are what the duplicate check compares against,
and — for properties that get stored — they are the exact vectors that end
up in the store (later: pgvector). Nothing downstream ever re-embeds or
recomputes them.

Duplicate detection has three outcomes (see Model/duplicate_verdict.py), and
NONE of them ever discards a property — a wrong auto-skip silently loses
real data with no way to recover it, which is strictly worse than asking a
human to glance at an extra row:
  - HIGH_CONFIDENCE_DUPLICATE: still stored, flagged needs_review=True (same
    review queue as UNCERTAIN) with review_notes and duplicate_of_record_id
    pointing at the specific existing property it matched, so a human makes
    the final call instead of the algorithm silently deleting a listing on
    its own.
  - HIGH_CONFIDENCE_NEW: stored as a normal property, needs_review=False.
  - UNCERTAIN: stored, flagged with needs_review=True, review_notes
    explaining why, and duplicate_of_record_id pointing at the candidate it
    was uncertain against (when the uncertainty came from a specific
    candidate rather than e.g. thin evidence generally).

needs_review is independent of review_status ("accepted" vs "outsider",
i.e. which of the Main/Outsider tabs a property belongs to) — a property
can arrive here already review_status="outsider", set by the LLM
structuring stage (Agent/WhatsAppDataFetchingAgent/property_structurer.py)
when it falls outside every client-selected area, and separately be
flagged needs_review=True by an UNCERTAIN or HIGH_CONFIDENCE_DUPLICATE
verdict. Both flags are shown at once; a human resolving the review flag
(see update_property below) never changes which tab (Main/Outsider) the
property is in — resolving needs_review and picking Main/Outsider happen
together only when a human explicitly does both (see the Needs review
dialog's Move to Main / Move to Outsider actions).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from Agent.WhatsAppDataFetchingAgent import property_structurer
from Database.property_repository import EDITABLE_CONTENT_FIELDS
from Middleware import step_logger
from Model.WhatsAppDataFetchingModel.duplicate_verdict import DuplicateVerdict
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty
from Model.WhatsAppDataFetchingModel.property_record import PropertyRecord
from Model.WhatsAppDataFetchingModel.structured_property import StructuredProperty
from Model.WhatsAppDataFetchingModel.whatsapp_message import WhatsAppChatMessage
from Service.WhatsAppDataFetchingService import (
    display_settings_service,
    duplicate_detection_service,
    embedding_service,
    property_vector_store,
    timestamp_formatting,
)

_duplicate_count = 0
_uncertain_count = 0
_outsider_count = 0

_NON_API_FIELDS = {"embedding", "field_embeddings", "embedding_model"}


def handle_batch_ready(batch: List[WhatsAppChatMessage]) -> None:
    """Called by the buffering stage whenever a batch is flushed (10
    messages gathered, or 1 hour elapsed). Already runs on its own thread
    (see message_buffer_service.py), so the blocking GLM call here never
    stalls WhatsApp message capture."""
    global _duplicate_count, _uncertain_count, _outsider_count

    step_logger.step(f"Sending batch of {len(batch)} qualified message(s) to GLM for structuring")
    properties = property_structurer.structure_batch(batch)

    accepted_count = 0
    uncertain_count_this_batch = 0
    duplicate_count_this_batch = 0
    outsider_count_this_batch = 0

    for prop in properties:
        embedded = _embed(prop)
        if embedded is None:
            continue

        is_outsider = embedded.review_status == "outsider"
        if is_outsider:
            outsider_count_this_batch += 1
            _outsider_count += 1

        result = duplicate_detection_service.check_duplicate(embedded)

        if result.verdict == DuplicateVerdict.HIGH_CONFIDENCE_DUPLICATE:
            duplicate_count_this_batch += 1
            _duplicate_count += 1
            # Flagged for review instead of skipped — an algorithm being
            # "sure" is still a guess, and a wrong skip here would delete a
            # real listing with no trace and no way for a human to catch it.
            # See the module docstring above.
            duplicate_reason = (
                f"High-confidence duplicate of message {result.matched_source_message_id!r}: {result.reason}"
            )
            embedded.needs_review = True
            embedded.duplicate_of_record_id = result.matched_record_id
            embedded.review_notes = (
                f"{embedded.review_notes} | {duplicate_reason}" if embedded.review_notes else duplicate_reason
            )
            step_logger.warn(
                f"High-confidence duplicate flagged for review, not skipped (source message "
                f"{embedded.source_message_id!r}): {result.reason}"
            )
        elif result.verdict == DuplicateVerdict.UNCERTAIN:
            uncertain_count_this_batch += 1
            _uncertain_count += 1
            # needs_review is independent of review_status (Main/Outsider) —
            # an outsider property flagged UNCERTAIN still shows up in the
            # Outsider tab, just also pulled into the review queue until a
            # human resolves it. review_notes is a single free-text field
            # shared by both judgments, so an outsider's existing reason
            # (set by the LLM structuring stage) is appended to rather than
            # overwritten — losing why it was marked outsider would be a
            # real regression, not just a cosmetic one.
            duplicate_reason = f"Possible duplicate of message {result.matched_source_message_id!r}: {result.reason}"
            embedded.needs_review = True
            embedded.duplicate_of_record_id = result.matched_record_id
            embedded.review_notes = (
                f"{embedded.review_notes} | {duplicate_reason}" if embedded.review_notes else duplicate_reason
            )
            step_logger.warn(
                f"Uncertain match, flagged for review (source message {embedded.source_message_id!r}): "
                f"{result.reason}"
            )
        else:
            step_logger.info(f"New property accepted (source message {embedded.source_message_id!r}): {result.reason}")

        property_vector_store.add_property(embedded)
        accepted_count += 1

    step_logger.success(
        f"Batch processed: {accepted_count} propert{'y' if accepted_count == 1 else 'ies'} stored "
        f"({uncertain_count_this_batch + duplicate_count_this_batch} flagged for review — "
        f"{duplicate_count_this_batch} high-confidence duplicate(s), {uncertain_count_this_batch} uncertain — "
        f"{outsider_count_this_batch} outsider), out of {len(batch)} message(s)"
    )


def _embed(prop: StructuredProperty) -> Optional[EmbeddedProperty]:
    try:
        vector = embedding_service.embed_property(prop)
        field_vectors = embedding_service.embed_property_fields(prop)
    except Exception as exc:  # noqa: BLE001
        # A single bad embedding must never cost the whole batch — the
        # other properties in it are still perfectly good.
        step_logger.error(f"Failed to embed property from message {prop.source_message_id!r}: {exc!r}")
        return None
    return EmbeddedProperty(
        **prop.model_dump(),
        embedding=vector,
        field_embeddings=field_vectors,
        embedding_model=embedding_service.EMBEDDING_MODEL_NAME,
    )


def get_properties(limit: int = 100) -> List[PropertyRecord]:
    """Backs the Properties/Landing Page/Inquiries pages' polling list —
    deliberately the lightweight summary (see property_vector_store.
    get_all_properties_summary): no photo bytes, just an accurate count.
    Callers that need one property's actual photos (opening its detail or
    Edit dialog) use get_property(record_id) below instead."""
    return [_to_record(prop, image_count=count) for prop, count in property_vector_store.get_all_properties_summary(limit=limit)]


def get_property(record_id: str) -> Optional[PropertyRecord]:
    """The single-record counterpart to get_properties — full content,
    photos included. Backs GET /properties/{record_id}."""
    prop = property_vector_store.get_property(record_id)
    if prop is None:
        return None
    _resolve_legacy_duplicate_match(prop)
    return _to_record(prop)


# Matches the message id duplicate_detection_service embeds into review_notes
# for both HIGH_CONFIDENCE_DUPLICATE ("High-confidence duplicate of message
# '...'") and UNCERTAIN ("Possible duplicate of message '...'") — see
# handle_batch_ready above for exactly where these strings are built.
_MATCHED_MESSAGE_ID_PATTERN = re.compile(r"duplicate of message '([^']+)'")


def _resolve_legacy_duplicate_match(prop: EmbeddedProperty) -> None:
    """Best-effort recovery for rows flagged before duplicate_of_record_id
    existed as a field: the specific candidate they were matched against was
    only ever recorded as free text inside review_notes (a source message
    id), never as a structured, directly-fetchable reference. Without this,
    the Needs review dialog's Comparison tab has nothing to show for any
    property flagged before this field was added, even though the exact
    match is right there in the text.

    Pulls the message id back out of review_notes and looks up a property
    that came from that same message, patching `prop.duplicate_of_record_id`
    in place — never persisted, so this costs nothing on the hot polling
    path (get_properties never calls this) and simply re-resolves on every
    detail-dialog open. A property flagged going forward already has this
    field set directly by handle_batch_ready, so this is purely a bridge for
    older rows, not the normal path."""
    if prop.duplicate_of_record_id or not prop.needs_review or not prop.review_notes:
        return
    match = _MATCHED_MESSAGE_ID_PATTERN.search(prop.review_notes)
    if not match:
        return
    source_message_id = match.group(1)
    candidates = [
        candidate
        for candidate in property_vector_store.find_by_source_message_id(source_message_id)
        if candidate.record_id != prop.record_id
    ]
    if not candidates:
        return
    if len(candidates) == 1:
        prop.duplicate_of_record_id = candidates[0].record_id
        return
    # A single WhatsApp message can yield more than one property (see
    # StructuredProperty.record_id's own comment) — when it does, the message
    # id alone doesn't say which of them was the actual match. Re-run the
    # same field-level scoring duplicate detection used at flag time (see
    # duplicate_detection_service.check_duplicate) against each candidate and
    # pick the highest-scoring one, rather than an arbitrary one.
    settings = duplicate_detection_service.get_settings()
    best = max(
        candidates,
        key=lambda candidate: duplicate_detection_service._score_candidate(prop, candidate, settings)[0],
    )
    prop.duplicate_of_record_id = best.record_id


def get_property_count() -> int:
    return property_vector_store.get_property_count()


def get_properties_version() -> str:
    return property_vector_store.get_properties_version()


def create_property(content_fields: Dict[str, Any]) -> PropertyRecord:
    """Backs the Properties page's Add dialog — a property entered by hand
    rather than extracted from a WhatsApp message. Every WhatsApp-metadata
    field StructuredProperty otherwise requires (sender, group, message
    text/timestamp) gets a placeholder here instead, since none of it
    exists for a manual entry; every content field is optional, matching
    the dialog itself (see property_controller.py's PropertyContentFields).

    Stored directly as review_status="accepted", needs_review=False — a
    human deliberately adding a property already knows about it, so running
    it back through duplicate detection would only risk second-guessing
    their own input."""
    fields = {key: value for key, value in content_fields.items() if key in EDITABLE_CONTENT_FIELDS}
    now = datetime.now(timezone.utc)
    structured = StructuredProperty(
        source_message_id=f"manual-{uuid.uuid4().hex}",
        group_name="Manually added",
        chat_type="personal",
        sender_name="Manual entry",
        sender_saved_name="Manual entry",
        sender_phone="manual",
        message_text=fields.get("description") or "Added manually via the Properties page.",
        message_timestamp=now,
        **fields,
    )
    # A manual Add that already includes a photo or reel link counts as
    # qualifying for the Landing Page page from the moment it's created —
    # same signal update_property computes below for an edit, just inlined
    # here since create_property builds the StructuredProperty directly
    # rather than going through content_updates.
    if structured.image_urls or structured.instagram_reel_url:
        structured.qualified_at = now
    embedded = _embed(structured)
    if embedded is None:
        # Embedding only fails on an unexpected model error (see _embed) —
        # exceedingly rare for a hand-typed property, but a manual Add must
        # never silently do nothing, so this is a real error, not a no-op.
        raise RuntimeError("Could not save this property — the embedding step failed. Please try again.")
    property_vector_store.add_property(embedded)
    return _to_record(embedded)


def update_property(
    record_id: str,
    review_status: Optional[str] = None,
    needs_review: Optional[bool] = None,
    content_updates: Optional[Dict[str, Any]] = None,
    landing_page: Optional[bool] = None,
) -> Optional[PropertyRecord]:
    """Backs four actions the UI offers on a stored property: "move to
    Main/Outsider" (review_status), "accept out of the review queue"
    (needs_review=False), the Properties page's Edit dialog
    (content_updates), and the Landing Page page's Send/Remove actions
    (landing_page) — any of the four can be passed alone, or together.
    Returns None if no property with this record_id exists.

    content_updates triggers a full embedding recompute, over the property's
    OTHER fields merged with the edit — never a partial/stale vector — so a
    hand-edited property stays exactly as comparable for duplicate detection
    as one the LLM structured, with no second, out-of-date vector left
    behind from before the edit.

    An edit that adds a photo or an Instagram reel link also bumps
    qualified_at — the Landing Page page's Ready to Add tab sorts newly
    qualified properties to the top by this, and Send/Remove deliberately
    never touch it (see StructuredProperty.qualified_at)."""
    embedding_kwargs: Dict[str, Any] = {}
    filtered_updates: Optional[Dict[str, Any]] = None
    qualified_at: Optional[datetime] = None
    if content_updates:
        filtered_updates = {key: value for key, value in content_updates.items() if key in EDITABLE_CONTENT_FIELDS}
        existing = property_vector_store.get_property(record_id)
        if existing is None:
            return None
        merged_data = existing.model_dump(exclude={"embedding", "field_embeddings", "embedding_model"})
        merged_data.update(filtered_updates)
        merged_structured = StructuredProperty(**merged_data)
        embedding_kwargs = {
            "embedding": embedding_service.embed_property(merged_structured),
            "field_embeddings": embedding_service.embed_property_fields(merged_structured),
            "embedding_model": embedding_service.EMBEDDING_MODEL_NAME,
        }
        if filtered_updates.get("image_urls") or filtered_updates.get("instagram_reel_url"):
            qualified_at = datetime.now(timezone.utc)

    updated = property_vector_store.update_property(
        record_id,
        review_status=review_status,
        needs_review=needs_review,
        content_updates=filtered_updates,
        on_landing_page=landing_page,
        qualified_at=qualified_at,
        **embedding_kwargs,
    )
    return _to_record(updated) if updated is not None else None


def delete_property(record_id: str) -> bool:
    return property_vector_store.delete_property(record_id)


def _to_record(prop: EmbeddedProperty, image_count: Optional[int] = None) -> PropertyRecord:
    use_24_hour_format = display_settings_service.get_use_24_hour_format()
    return PropertyRecord(
        **prop.model_dump(exclude=_NON_API_FIELDS),
        formatted_timestamp=timestamp_formatting.format_ist(prop.message_timestamp, use_24_hour_format),
        # Passed explicitly by get_properties, whose summary rows have
        # image_urls=[] (see property_vector_store.get_all_properties_summary)
        # — falling back to len(prop.image_urls) here would silently report
        # zero photos for every property on that path. Every other caller
        # (create/update/get_property) loads full rows, so the fallback is
        # exact for them.
        image_count=image_count if image_count is not None else len(prop.image_urls),
    )


def get_duplicate_count() -> int:
    return _duplicate_count


def get_uncertain_count() -> int:
    return _uncertain_count


def get_outsider_count() -> int:
    return _outsider_count
