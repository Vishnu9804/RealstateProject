"""LLM-driven structuring stage (Stage 3 — "LLM Processing" in the
architecture diagram): turns a batch of up to 10 free-form qualified
WhatsApp messages into StructuredProperty records using Gemini, in a single
prompt per batch so the LLM's context window is used efficiently instead of
spending one request per message.

Genuinely agentic (LLM-driven) code — this is what Agent/ is reserved for,
unlike the deterministic Service/ modules.

Design choice: Gemini is only asked to extract the free-text fields that
actually require language understanding (see gemini_extraction_schema.py).
Everything already known for certain from WhatsApp itself (sender name,
saved contact name, group name, timestamp) is merged in afterwards by this
module, not re-derived by the LLM — asking an LLM to faithfully copy data it
didn't need to extract only adds a chance of transcription error for no
benefit, and timezone/date-format conversion is exact arithmetic an LLM is
the wrong tool for (see Service/timestamp_formatting.py).
"""

from __future__ import annotations

import json
from typing import List, Optional

from google import genai
from google.genai import types
from pydantic import ValidationError

from Agent.gemini_extraction_schema import GeminiPropertyExtraction
from Config.settings import get_settings
from Middleware import step_logger
from Model.structured_property import StructuredProperty
from Model.whatsapp_message import WhatsAppChatMessage

_client: Optional[genai.Client] = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = get_settings().gemini_api_key
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set — add it to Backend/.env before the LLM stage can run."
            )
        _client = genai.Client(api_key=api_key)
    return _client


def structure_batch(batch: List[WhatsAppChatMessage]) -> List[StructuredProperty]:
    """Sends one batch (up to 10 messages) to Gemini in a single prompt and
    returns StructuredProperty records for the messages that turned out to
    be actual listings. Never raises: a batch that fails outright (missing/
    bad key, network error, unparseable response) is logged and skipped
    rather than crashing the caller. It is simply lost for now — there is
    nowhere durable to retry it from until the database step exists."""
    if not batch:
        return []

    try:
        client = _get_client()
        response = client.models.generate_content(
            model=get_settings().gemini_model,
            contents=_build_prompt(batch),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=list[GeminiPropertyExtraction],
                temperature=0.1,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Gemini structuring request failed for a batch of {len(batch)}: {exc!r}")
        return []

    extractions = _parse_extractions(response, len(batch))
    return _merge_with_message_data(extractions, batch)


def _build_prompt(batch: List[WhatsAppChatMessage]) -> str:
    lines = [
        "You are a real estate data-extraction assistant. Below is a batch of raw "
        "WhatsApp messages from Indian real estate broker/community groups. Each "
        "message has already been confirmed to mention a tracked area, but not "
        "every message is necessarily an actual property listing — some may be "
        "questions, requests, or unrelated remarks that happen to mention the area.",
        "",
        "For EACH message below, return exactly one JSON object, in the same order, "
        "with \"source_message_id\" matching the message's id exactly.",
        "",
        "Never invent details that are not present in the message text. If a field "
        "is not mentioned, use null rather than guessing.",
        "",
        "Messages:",
    ]
    for message in batch:
        text = message.text.replace("\n", " ").strip()
        lines.append(f'- id={message.message_id} | group="{message.chat_name}" | text: "{text}"')
    return "\n".join(lines)


def _parse_extractions(response, batch_size: int) -> List[GeminiPropertyExtraction]:
    parsed = getattr(response, "parsed", None)
    if parsed:
        return [item for item in parsed if isinstance(item, GeminiPropertyExtraction)]

    # Fall back to manual parsing if the SDK couldn't auto-parse (e.g. the
    # model's output didn't quite match the schema).
    raw_text = getattr(response, "text", None)
    if not raw_text:
        step_logger.error(f"Gemini returned no usable content for a batch of {batch_size}.")
        return []

    try:
        raw_items = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        step_logger.error(f"Gemini response was not valid JSON for a batch of {batch_size}: {exc}")
        return []

    extractions: List[GeminiPropertyExtraction] = []
    for raw_item in raw_items:
        try:
            extractions.append(GeminiPropertyExtraction.model_validate(raw_item))
        except ValidationError as exc:
            step_logger.warn(f"Skipping one malformed extraction from Gemini: {exc}")
    return extractions


def _merge_with_message_data(
    extractions: List[GeminiPropertyExtraction], batch: List[WhatsAppChatMessage]
) -> List[StructuredProperty]:
    messages_by_id = {message.message_id: message for message in batch}
    seen_ids = set()
    properties: List[StructuredProperty] = []

    for extraction in extractions:
        message = messages_by_id.get(extraction.source_message_id)
        if message is None:
            step_logger.warn(
                "Gemini returned an extraction for an unknown message id "
                f"({extraction.source_message_id!r}); discarding it."
            )
            continue
        seen_ids.add(extraction.source_message_id)

        if not extraction.is_property_listing:
            step_logger.info(
                f"Skipped (not a listing): {extraction.skip_reason or 'no reason given'} — {message.text[:80]!r}"
            )
            continue

        properties.append(
            StructuredProperty(
                source_message_id=message.message_id,
                property_type=extraction.property_type,
                bhk=extraction.bhk,
                area_name=extraction.area_name,
                address=extraction.address,
                price_text=extraction.price_text,
                price_amount_inr=extraction.price_amount_inr,
                contact_name=extraction.contact_name,
                contact_phone=extraction.contact_phone,
                description=extraction.description,
                group_name=message.chat_name,
                chat_type=message.chat_type,
                sender_name=message.sender_name,
                sender_saved_name=message.sender_saved_name,
                sender_phone=message.sender_phone,
                message_text=message.text,
                message_timestamp=message.received_at,
            )
        )

    for missing_id in set(messages_by_id) - seen_ids:
        step_logger.warn(f"Gemini did not return anything for message id {missing_id!r} — dropped from this batch.")

    return properties
