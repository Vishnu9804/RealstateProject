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
the wrong tool for (see Service/WhatsAppDataFetchingService/timestamp_formatting.py).
"""

from __future__ import annotations

import json
from typing import List, Optional

from google import genai
from google.genai import types
from pydantic import ValidationError

from Agent.WhatsAppDataFetchingAgent.gemini_extraction_schema import GeminiPropertyExtraction, GeminiPropertyListing
from Config.settings import get_settings
from Middleware import step_logger
from Model.WhatsAppDataFetchingModel.structured_property import StructuredProperty
from Model.WhatsAppDataFetchingModel.whatsapp_message import WhatsAppChatMessage
from Service.WhatsAppDataFetchingService import area_filter_service

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
    tracked_areas = area_filter_service.get_area_keywords()
    tracked_areas_list = ", ".join(tracked_areas) if tracked_areas else "(none configured)"
    lines = [
        "You are a real estate data-extraction assistant. Below is a batch of raw "
        "WhatsApp messages from Indian real estate broker/community groups. Each "
        "message has already been confirmed to mention at least one tracked area "
        "SOMEWHERE in its text, but not every message is necessarily an actual "
        "property listing — some may be questions, requests, or unrelated remarks "
        "that happen to mention the area.",
        "",
        f"Client-selected tracked areas: {tracked_areas_list}.",
        "",
        "For EACH message below, return exactly one JSON object, in the same order, "
        "with \"source_message_id\" matching the message's id exactly.",
        "",
        "IMPORTANT — a single message can advertise MORE THAN ONE property (e.g. a "
        "broker listing several separate flats, possibly in different areas, in one "
        "text). Put every distinct property mentioned in that message's "
        "\"properties\" list, one entry per property — almost always this list has "
        "exactly one entry, but use more than one only when the message clearly "
        "describes separate properties (different society/area, different BHK, or "
        "different price for each). Do NOT split one property's own details across "
        "multiple entries.",
        "",
        "IMPORTANT — AREA FILTER: only include a property in \"properties\" if its "
        "own area_name is one of the client-selected tracked areas listed above "
        "(matched by locality, ignoring case). The message as a whole was let "
        "through because it mentions a tracked area SOMEWHERE, but if the message "
        "lists several properties in different localities, silently drop every "
        "property whose own area is NOT in the tracked list — do not include it in "
        "\"properties\" at all, even though the message qualified. Only set "
        "is_property_listing to false (with a skip_reason) if NONE of the "
        "message's properties are in a tracked area, or the message isn't a "
        "listing at all.",
        "",
        "Keep society_name (a specific named building/project/society, e.g. \"Black "
        "Residency\") and area_name (the general locality, e.g. \"Althan\") strictly "
        "separate — do not put a locality in society_name or a building name in "
        "area_name. Only fill carpet_area_sqft if an explicit square-footage number "
        "is stated in the message; never estimate it from the BHK.",
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

        if not extraction.is_property_listing or not extraction.properties:
            step_logger.info(
                f"Skipped (not a listing): {extraction.skip_reason or 'no reason given'} — {message.text[:80]!r}"
            )
            continue

        if len(extraction.properties) > 1:
            step_logger.info(
                f"Message {message.message_id!r} contains {len(extraction.properties)} distinct properties — "
                "structuring each separately."
            )

        for listing in extraction.properties:
            if not _listing_is_in_tracked_area(listing):
                step_logger.info(
                    f"Dropped a property from message {message.message_id!r}: its area "
                    f"({listing.area_name!r}) is not one of the client-selected tracked areas."
                )
                continue
            properties.append(_to_structured_property(listing, message))

    for missing_id in set(messages_by_id) - seen_ids:
        step_logger.warn(f"Gemini did not return anything for message id {missing_id!r} — dropped from this batch.")

    return properties


def _listing_is_in_tracked_area(listing: GeminiPropertyListing) -> bool:
    """Deterministic safety net behind the prompt's area instruction: a
    message qualifies for the pipeline if ANY of its text mentions a tracked
    area (see Service/WhatsAppDataFetchingService/area_filter_service.py and
    whatsapp_service.py), but with multiple properties per message that is no
    longer good enough per-property — a message about Althan can still
    mention an unrelated Vadod property, which must never reach the
    database. Re-runs the exact same keyword match, scoped to just this
    listing's own locality fields, so a listing survives only if IT (not
    just the message) is actually in a client-selected area. Relying on the
    LLM alone to honor this isn't reliable enough to guarantee it."""
    haystack = " ".join(
        filter(None, [listing.area_name, listing.society_name, listing.address, listing.description])
    )
    return area_filter_service.is_qualified(haystack)


def _to_structured_property(listing: GeminiPropertyListing, message: WhatsAppChatMessage) -> StructuredProperty:
    """Builds one StructuredProperty from one extracted listing, merged with
    the WhatsApp metadata shared by every listing pulled from that same
    message. Two listings from one message become two fully independent
    StructuredProperty records here — each is embedded and duplicate-checked
    on its own downstream (see property_pipeline_service.handle_batch_ready),
    exactly as if they had arrived in separate messages."""
    return StructuredProperty(
        source_message_id=message.message_id,
        property_type=listing.property_type,
        bhk=listing.bhk,
        society_name=listing.society_name,
        area_name=listing.area_name,
        address=listing.address,
        carpet_area_sqft=listing.carpet_area_sqft,
        price_text=listing.price_text,
        price_amount_inr=listing.price_amount_inr,
        contact_name=listing.contact_name,
        contact_phone=listing.contact_phone,
        description=listing.description,
        group_name=message.chat_name,
        chat_type=message.chat_type,
        sender_name=message.sender_name,
        sender_saved_name=message.sender_saved_name,
        sender_phone=message.sender_phone,
        message_text=message.text,
        message_timestamp=message.received_at,
    )
