"""Owns the "back half" of the property pipeline: receiving flushed message
batches from the buffering stage (Service/message_buffer_service.py),
running them through the LLM structuring stage (Agent/
property_structurer.py), and holding the resulting StructuredProperty
records for the Controller layer to read.

No DB yet by design (see project instructions) — an in-memory list, same as
the rest of the pipeline; the database step swaps this out without touching
callers.
"""

from __future__ import annotations

from typing import List

from Agent import property_structurer
from Middleware import step_logger
from Model.property_record import PropertyRecord
from Model.structured_property import StructuredProperty
from Model.whatsapp_message import WhatsAppChatMessage
from Service import display_settings_service, timestamp_formatting

_MAX_STORED_PROPERTIES = 1000

_structured_properties: List[StructuredProperty] = []


def handle_batch_ready(batch: List[WhatsAppChatMessage]) -> None:
    """Called by the buffering stage whenever a batch is flushed (10
    messages gathered, or 1 hour elapsed). Already runs on its own thread
    (see message_buffer_service.py), so the blocking Gemini call here never
    stalls WhatsApp message capture."""
    step_logger.step(f"Sending batch of {len(batch)} qualified message(s) to Gemini for structuring")
    properties = property_structurer.structure_batch(batch)

    _structured_properties.extend(properties)
    if len(_structured_properties) > _MAX_STORED_PROPERTIES:
        del _structured_properties[: len(_structured_properties) - _MAX_STORED_PROPERTIES]

    step_logger.success(
        f"Structured {len(properties)} propert{'y' if len(properties) == 1 else 'ies'} "
        f"out of {len(batch)} message(s) in this batch"
    )


def get_properties(limit: int = 100) -> List[PropertyRecord]:
    use_24_hour_format = display_settings_service.get_use_24_hour_format()
    return [
        PropertyRecord(
            **prop.model_dump(),
            formatted_timestamp=timestamp_formatting.format_ist(prop.message_timestamp, use_24_hour_format),
        )
        for prop in _structured_properties[-limit:]
    ]


def get_property_count() -> int:
    return len(_structured_properties)
