"""Owns the "back half" of the property pipeline: receiving flushed message
batches from the buffering stage (Service/message_buffer_service.py),
running them through the LLM structuring stage (Agent/
property_structurer.py), the embedding stage (Service/embedding_service.py),
and the duplicate-detection stage (Service/duplicate_detection_service.py),
then storing the result (Service/property_vector_store.py) for the
Controller layer to read.

Every structured property is embedded exactly once, right here, right after
structuring. Those vectors are what the duplicate check compares against,
and — for properties that get stored — they are the exact vectors that end
up in the store (later: pgvector). Nothing downstream ever re-embeds or
recomputes them.

Duplicate detection now has three outcomes, not two (see Model/
duplicate_verdict.py):
  - HIGH_CONFIDENCE_DUPLICATE: skipped, not stored.
  - HIGH_CONFIDENCE_NEW: stored as a normal, accepted property.
  - UNCERTAIN: still stored (never silently discarded — it's real data),
    but flagged with review_status="needs_review" and review_notes
    explaining why, so nothing gets lost while still surfacing the
    ambiguity for a human to resolve later.
"""

from __future__ import annotations

from typing import List, Optional

from Agent import property_structurer
from Middleware import step_logger
from Model.duplicate_verdict import DuplicateVerdict
from Model.embedded_property import EmbeddedProperty
from Model.property_record import PropertyRecord
from Model.structured_property import StructuredProperty
from Model.whatsapp_message import WhatsAppChatMessage
from Service import (
    display_settings_service,
    duplicate_detection_service,
    embedding_service,
    property_vector_store,
    timestamp_formatting,
)

_duplicate_count = 0
_uncertain_count = 0

_NON_API_FIELDS = {"embedding", "field_embeddings", "embedding_model"}


def handle_batch_ready(batch: List[WhatsAppChatMessage]) -> None:
    """Called by the buffering stage whenever a batch is flushed (10
    messages gathered, or 1 hour elapsed). Already runs on its own thread
    (see message_buffer_service.py), so the blocking Gemini call here never
    stalls WhatsApp message capture."""
    global _duplicate_count, _uncertain_count

    step_logger.step(f"Sending batch of {len(batch)} qualified message(s) to Gemini for structuring")
    properties = property_structurer.structure_batch(batch)

    accepted_count = 0
    uncertain_count_this_batch = 0
    duplicate_count_this_batch = 0

    for prop in properties:
        embedded = _embed(prop)
        if embedded is None:
            continue

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
            embedded.review_status = "needs_review"
            embedded.review_notes = (
                f"Possible duplicate of message {result.matched_source_message_id!r}: {result.reason}"
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
        f"({uncertain_count_this_batch} flagged for review), {duplicate_count_this_batch} duplicate(s) skipped, "
        f"out of {len(batch)} message(s)"
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
    use_24_hour_format = display_settings_service.get_use_24_hour_format()
    return [
        PropertyRecord(
            **prop.model_dump(exclude=_NON_API_FIELDS),
            formatted_timestamp=timestamp_formatting.format_ist(prop.message_timestamp, use_24_hour_format),
        )
        for prop in property_vector_store.get_all_properties(limit=limit)
    ]


def get_property_count() -> int:
    return property_vector_store.get_property_count()


def get_duplicate_count() -> int:
    return _duplicate_count


def get_uncertain_count() -> int:
    return _uncertain_count
