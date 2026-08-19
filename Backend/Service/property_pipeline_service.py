"""Owns the "back half" of the property pipeline: receiving flushed message
batches from the buffering stage (Service/message_buffer_service.py),
running them through the LLM structuring stage (Agent/
property_structurer.py) and the embedding stage (Service/
embedding_service.py), and holding the resulting EmbeddedProperty records
for the Controller layer to read.

Every structured property is embedded exactly once, right here, right after
structuring. That one vector is what the duplicate-check step (next)
compares against, and it is the exact vector the database step later writes
into pgvector — nothing downstream ever re-embeds.

No DB yet by design (see project instructions) — an in-memory list, same as
the rest of the pipeline; the database step swaps this out without touching
callers.
"""

from __future__ import annotations

from typing import List

from Agent import property_structurer
from Middleware import step_logger
from Model.embedded_property import EmbeddedProperty
from Model.property_record import PropertyRecord
from Model.structured_property import StructuredProperty
from Model.whatsapp_message import WhatsAppChatMessage
from Service import display_settings_service, embedding_service, timestamp_formatting

_MAX_STORED_PROPERTIES = 1000

_structured_properties: List[EmbeddedProperty] = []


def handle_batch_ready(batch: List[WhatsAppChatMessage]) -> None:
    """Called by the buffering stage whenever a batch is flushed (10
    messages gathered, or 1 hour elapsed). Already runs on its own thread
    (see message_buffer_service.py), so the blocking Gemini call here never
    stalls WhatsApp message capture."""
    step_logger.step(f"Sending batch of {len(batch)} qualified message(s) to Gemini for structuring")
    properties = property_structurer.structure_batch(batch)

    embedded_properties = _embed_all(properties)
    _structured_properties.extend(embedded_properties)
    if len(_structured_properties) > _MAX_STORED_PROPERTIES:
        del _structured_properties[: len(_structured_properties) - _MAX_STORED_PROPERTIES]

    step_logger.success(
        f"Structured and embedded {len(embedded_properties)} propert"
        f"{'y' if len(embedded_properties) == 1 else 'ies'} out of {len(batch)} message(s) in this batch"
    )


def _embed_all(properties: List[StructuredProperty]) -> List[EmbeddedProperty]:
    embedded: List[EmbeddedProperty] = []
    for prop in properties:
        try:
            vector = embedding_service.embed_property(prop)
        except Exception as exc:  # noqa: BLE001
            # A single bad embedding must never cost the whole batch — the
            # other properties in it are still perfectly good.
            step_logger.error(f"Failed to embed property from message {prop.source_message_id!r}: {exc!r}")
            continue
        embedded.append(
            EmbeddedProperty(
                **prop.model_dump(), embedding=vector, embedding_model=embedding_service.EMBEDDING_MODEL_NAME
            )
        )
    return embedded


def get_properties(limit: int = 100) -> List[PropertyRecord]:
    use_24_hour_format = display_settings_service.get_use_24_hour_format()
    return [
        PropertyRecord(
            **prop.model_dump(exclude={"embedding", "embedding_model"}),
            formatted_timestamp=timestamp_formatting.format_ist(prop.message_timestamp, use_24_hour_format),
        )
        for prop in _structured_properties[-limit:]
    ]


def get_property_count() -> int:
    return len(_structured_properties)
