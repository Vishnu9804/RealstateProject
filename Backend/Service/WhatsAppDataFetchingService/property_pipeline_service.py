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

Duplicate detection has three outcomes (see Model/duplicate_verdict.py):
  - HIGH_CONFIDENCE_DUPLICATE: skipped, not stored.
  - HIGH_CONFIDENCE_NEW: stored as a normal property, needs_review=False.
  - UNCERTAIN: still stored (never silently discarded — it's real data),
    but flagged with needs_review=True and review_notes explaining why, so
    nothing gets lost while still surfacing the ambiguity for a human to
    resolve later.

needs_review is independent of review_status ("accepted" vs "outsider",
i.e. which of the Main/Outsider tabs a property belongs to) — a property
can arrive here already review_status="outsider", set by the LLM
structuring stage (Agent/WhatsAppDataFetchingAgent/property_structurer.py)
when it falls outside every client-selected area, and separately be
flagged needs_review=True by an UNCERTAIN duplicate verdict. Both flags are
shown at once; a human resolving the review flag (see accept_property
below) never changes which tab (Main/Outsider) the property is in. A
HIGH_CONFIDENCE_DUPLICATE outsider is still skipped like any other
duplicate — being outside the service area doesn't make an exact repeat
worth storing twice.
"""

from __future__ import annotations

from typing import List, Optional

from Agent.WhatsAppDataFetchingAgent import property_structurer
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
            step_logger.info(
                f"Duplicate property skipped (source message {embedded.source_message_id!r}): {result.reason}"
            )
            continue

        if result.verdict == DuplicateVerdict.UNCERTAIN:
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
        f"({uncertain_count_this_batch} flagged for review, {outsider_count_this_batch} outsider), "
        f"{duplicate_count_this_batch} duplicate(s) skipped, out of {len(batch)} message(s)"
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
    return [_to_record(prop) for prop in property_vector_store.get_all_properties(limit=limit)]


def get_property_count() -> int:
    return property_vector_store.get_property_count()


def update_property(
    record_id: str, review_status: Optional[str] = None, needs_review: Optional[bool] = None
) -> Optional[PropertyRecord]:
    """Backs both actions the UI offers on a stored property: "move to
    Main/Outsider" (review_status) and "accept out of the review queue"
    (needs_review=False) — either can be passed alone, or both together.
    Returns None if no property with this record_id exists."""
    updated = property_vector_store.update_property(record_id, review_status=review_status, needs_review=needs_review)
    return _to_record(updated) if updated is not None else None


def delete_property(record_id: str) -> bool:
    return property_vector_store.delete_property(record_id)


def _to_record(prop: EmbeddedProperty) -> PropertyRecord:
    use_24_hour_format = display_settings_service.get_use_24_hour_format()
    return PropertyRecord(
        **prop.model_dump(exclude=_NON_API_FIELDS),
        formatted_timestamp=timestamp_formatting.format_ist(prop.message_timestamp, use_24_hour_format),
    )


def get_duplicate_count() -> int:
    return _duplicate_count


def get_uncertain_count() -> int:
    return _uncertain_count


def get_outsider_count() -> int:
    return _outsider_count
