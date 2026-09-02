"""LLM-driven structuring stage (Stage 3 — "LLM Processing" in the
architecture diagram): turns a batch of up to 10 free-form qualified
WhatsApp messages into StructuredProperty records using GLM-4.7-FlashX (via
Z.ai's OpenAI-compatible chat-completions API), in a single prompt per batch
so the LLM's context window is used efficiently instead of spending one
request per message.

Genuinely agentic (LLM-driven) code — this is what Agent/ is reserved for,
unlike the deterministic Service/ modules.

Design choice: the LLM is only asked to extract the free-text fields that
actually require language understanding (see glm_extraction_schema.py).
Everything already known for certain from WhatsApp itself (sender name,
saved contact name, group name, timestamp) is merged in afterwards by this
module, not re-derived by the LLM — asking an LLM to faithfully copy data it
didn't need to extract only adds a chance of transcription error for no
benefit, and timezone/date-format conversion is exact arithmetic an LLM is
the wrong tool for (see Service/WhatsAppDataFetchingService/timestamp_formatting.py).

Previously ran on Gemini (google-genai's SDK gave native `response_schema`
enforcement); moved to GLM-4.7-FlashX for materially lower per-token cost at
production volume. Z.ai's API only offers `response_format: json_object`
(a loose "valid JSON" guarantee, not a bound Pydantic schema), so the exact
output shape is spelled out in the prompt instead and validated manually on
the way back in (see _parse_extractions) — the schema classes in
glm_extraction_schema.py double as that validator.
"""

from __future__ import annotations

import json
import re
import time
from typing import List, Optional

import httpx
from pydantic import ValidationError

from Agent.WhatsAppDataFetchingAgent.glm_extraction_schema import (
    GLMExtractionResponse,
    GLMPropertyExtraction,
    GLMPropertyListing,
)
from Config.settings import get_settings
from Middleware import step_logger
from Model.WhatsAppDataFetchingModel.structured_property import StructuredProperty
from Model.WhatsAppDataFetchingModel.whatsapp_message import WhatsAppChatMessage
from Service.WhatsAppDataFetchingService import area_filter_service

_client: Optional[httpx.Client] = None
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

# A batch that fails outright is otherwise lost for good (see structure_batch's
# docstring) — there is nowhere durable to retry it from once this function
# returns. Measured real latency for a 3-property message is ~11s, tens of
# times under the 180s per-attempt timeout, so a request that actually times
# out or drops is a transient blip on Z.ai's side, not this prompt being too
# large — worth one or two retries rather than silently losing real listings
# over a network hiccup. Deliberately small: this only fires on failure, so
# it costs nothing on the (overwhelming majority of) calls that succeed on
# the first attempt.
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = [3.0, 10.0]


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        api_key = get_settings().zai_api_key
        if not api_key:
            raise RuntimeError(
                "ZAI_API_KEY is not set — add it to Backend/.env before the LLM stage can run."
            )
        _client = httpx.Client(
            base_url=get_settings().zai_base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            # Measured against the real API: a single message with ~10
            # properties took 45-80s to fully decode even with "thinking"
            # disabled (see structure_batch) — plain generation time for a
            # large structured JSON reply, not something a smaller timeout
            # can fix. This call already runs on its own background thread
            # (see structure_batch's docstring) and never blocks WhatsApp
            # message capture, so there's no reason to cut it close: 180s
            # leaves real headroom for a full 10-message batch rather than
            # discarding an otherwise-successful response.
            timeout=180.0,
        )
    return _client


def structure_batch(batch: List[WhatsAppChatMessage]) -> List[StructuredProperty]:
    """Sends one batch (up to 10 messages) to GLM in a single prompt and
    returns StructuredProperty records for the messages that turned out to
    be actual listings. Never raises: a batch that still fails after retries
    (bad key, unparseable response, a request that keeps failing) is logged
    and skipped rather than crashing the caller. It is simply lost for now —
    there is nowhere durable to retry it from once this function returns
    (until the database gets a durable job queue)."""
    if not batch:
        return []

    http_response = _post_with_retries(batch)
    if http_response is None:
        return []

    extractions = _parse_extractions(http_response, len(batch))
    return _merge_with_message_data(extractions, batch)


def _post_with_retries(batch: List[WhatsAppChatMessage]) -> Optional[httpx.Response]:
    """Posts the structuring request, retrying up to _MAX_ATTEMPTS times on
    transient failures only: a timeout/connection drop, or a 5xx/429 from
    Z.ai — never on a 4xx like bad auth or a malformed request, which will
    just fail identically (and cost identically) on every retry. Returns
    None only once every attempt has failed."""
    client = _get_client()
    request_body = {
        "model": get_settings().zai_model,
        "messages": [
            {"role": "system", "content": _build_system_prompt()},
            {"role": "user", "content": _build_user_prompt(batch)},
        ],
        # 0.0, not 0.1: this is a classification/extraction task, not
        # a creative one — nothing benefits from sampling diversity,
        # and the SERVICE AREA MATCHING step (PART 3) especially
        # needs the most repeatable reasoning this model can give.
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
        # GLM-4.7-FlashX runs chain-of-thought reasoning by default,
        # which is built for open-ended/agentic tasks — it adds
        # significant latency for no benefit on a fixed-shape
        # extraction task like this one (it was the direct cause of
        # timeouts on batches with several properties in one
        # message) and is turned off here for speed.
        "thinking": {"type": "disabled"},
    }

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            http_response = client.post("chat/completions", json=request_body)
            http_response.raise_for_status()
            return http_response
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            transient = status == 429 or status >= 500
            if not transient or attempt == _MAX_ATTEMPTS:
                step_logger.error(
                    f"GLM structuring request failed for a batch of {len(batch)} "
                    f"(attempt {attempt}/{_MAX_ATTEMPTS}, HTTP {status}): {exc!r}"
                )
                return None
            step_logger.warn(
                f"GLM structuring request got HTTP {status} for a batch of {len(batch)} "
                f"(attempt {attempt}/{_MAX_ATTEMPTS}) — retrying, this is a Z.ai-side status "
                "that's worth another try, not a request we're sending wrong."
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt == _MAX_ATTEMPTS:
                step_logger.error(
                    f"GLM structuring request failed for a batch of {len(batch)} "
                    f"(attempt {attempt}/{_MAX_ATTEMPTS}): {exc!r}"
                )
                return None
            step_logger.warn(
                f"GLM structuring request timed out/dropped for a batch of {len(batch)} "
                f"(attempt {attempt}/{_MAX_ATTEMPTS}) — retrying rather than losing these "
                "properties to what measured latency says is a transient blip, not the "
                "request itself being too slow to ever finish."
            )
        except Exception as exc:  # noqa: BLE001
            # Anything else (bad API key, DNS failure, ...) will fail the
            # exact same way on every retry — burning two more paid calls to
            # confirm that would just be wasted spend.
            step_logger.error(f"GLM structuring request failed for a batch of {len(batch)}: {exc!r}")
            return None

        time.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])

    return None


def _build_system_prompt() -> str:
    return "\n".join(
        [
            "You are a real estate data-extraction assistant. You will be given a batch "
            "of raw WhatsApp messages from Indian real estate broker/community groups in "
            "Surat, India, each already confirmed by a broad, non-area-specific keyword "
            "pre-filter to merely LOOK property-related SOMEWHERE in its text (a BHK/RK "
            "mention, an area/price/location word, etc — that pre-filter says nothing "
            "about whether it's a real listing or which locality it's in). Your job has "
            "three parts: (1) decide whether each message is actually a property listing, "
            "(2) if it is, extract structured details from it, and (3) for each extracted "
            "property, decide whether it falls inside one of the client's selected areas.",
            "",
            "=== PART 1 — is_property_listing classification ===",
            "",
            "The keyword match that got a message into this batch is a rough, generic "
            "pre-filter (BHK/RK, area/price/location words) — it says nothing about "
            "whether the message is actually about a property transaction. That judgment "
            "is entirely yours, and it is the most important part of this task: get it "
            "wrong and junk data reaches a real database.",
            "",
            "Set is_property_listing to TRUE only if the message is a genuine BUY, SELL, "
            "or RENT real-estate listing or request — someone (broker, owner, agent, or "
            "a buyer/tenant themselves) actively offering a specific property, or actively "
            "asking to buy/rent one with concrete requirements. Examples that ARE listings: "
            "\"2BHK flat available for rent in Vesu, 15000/month, contact 98xxxxxxx\", "
            "\"Need 3BHK for rent near Althan, budget 20k, family only\", \"Shop for sale in "
            "VIP Road, 400 sqft, 55 Lakh, urgent\".",
            "",
            "Set is_property_listing to FALSE for everything else, even if a tracked area "
            "is mentioned. In particular, FALSE covers: greetings, festival wishes, jokes, "
            "forwards, or general chit-chat that happens to name an area; traffic, weather, "
            "local news, politics, or community-event messages about an area; a question "
            "about market rates/trends with no specific property being offered or sought "
            "(\"what's the rate in Althan these days?\"); a personal or business address "
            "mentioned for an unrelated reason (a delivery, a meeting spot); opinions or "
            "complaints about a builder or the market with no actual listing attached; and "
            "any case where the area word only matches because it's part of an unrelated "
            "person's name, business name, or proper noun.",
            "",
            "If you are genuinely unsure whether a message is a real listing/request, set "
            "is_property_listing to FALSE rather than guessing true. A real listing that "
            "gets skipped is recoverable later; a non-listing that gets structured and "
            "saved is bad data a human then has to notice and clean up by hand. Precision "
            "matters more than recall here.",
            "",
            "IMPORTANT — do not use area relevance as a signal for this decision at all. "
            "Whether the mentioned area is one of the client's tracked areas is completely "
            "irrelevant to is_property_listing; judge only whether it's a genuine listing.",
            "",
            "=== PART 2 — extraction (only for messages that ARE listings) ===",
            "",
            "A single message can advertise MORE THAN ONE property (e.g. a broker listing "
            "several separate flats, possibly in different areas, in one text). Put every "
            "distinct property mentioned in that message's \"properties\" list, one entry "
            "per property — almost always this list has exactly one entry, but use more "
            "than one only when the message clearly describes separate properties "
            "(different society/area, different BHK, or different price for each). Do NOT "
            "split one property's own details across multiple entries.",
            "",
            "Extract EVERY distinct property mentioned in a message that IS a listing, no "
            "matter which locality it's in, even if its area looks unrelated to the "
            "client's selected areas — do not silently drop or skip a property because you "
            "think it's in the wrong area. Area relevance is decided explicitly, per "
            "property, in PART 3 below (in_service_area) — that field is where an "
            "out-of-area property gets flagged, never by omitting it from \"properties\".",
            "",
            "Keep society_name (a specific named building/project/society, e.g. \"Black "
            "Residency\") and area_name (the general locality, e.g. \"Althan\") strictly "
            "separate — do not put a locality in society_name or a building name in "
            "area_name. Only fill carpet_area_sqft if an explicit square-footage number is "
            "stated in the message; never estimate it from the BHK.",
            "",
            "Never invent details that are not present in the message text. If a field is "
            "not mentioned, use null rather than guessing.",
            "",
            "=== PART 3 — SERVICE AREA MATCHING (in_service_area) ===",
            "",
            "The client only serves Surat, India, and only wants properties inside the "
            "specific areas they selected in their settings (given to you below as "
            "\"Client-selected areas\"). This is the part small/fast models get wrong most "
            "often — they compare area strings shallowly (\"VIP Road\" != \"Vesu\", so FALSE) "
            "instead of actually recalling that VIP Road is a well-known road INSIDE Vesu. "
            "Follow this exact procedure to avoid that mistake:",
            "",
            "STEP A — RECALL FIRST (do this once, before looking at any message). In the "
            "top-level \"area_knowledge\" field, write one short line per client-selected area, "
            "actively recalling from your own knowledge of Surat whatever you know about it: "
            "specifically its own well-known roads, landmarks, malls, schools, and society "
            "clusters that sit WITHIN it and would themselves be described as being in that "
            "area. Do this for EVERY client-selected area, even ones you feel you know little "
            "about — write down whatever you do know, however partial. This is a deliberate "
            "generate-the-knowledge-before-you-use-it step: writing it down first, in full, "
            "measurably beats trying to recall the same fact silently at the moment you need "
            "it. Do not skip this or leave it a stub.",
            "",
            "STRICT SCOPE for STEP A: only list something as belonging to a selected area if "
            "it is genuinely a PART of that area — a road that runs through it, a landmark "
            "situated in it, a society/complex physically inside it. Do NOT list a separate, "
            "independently-named Surat locality just because it happens to neighbor or sit "
            "close to a selected area — e.g. if Piplod and Vesu are two distinct, separately-"
            "named localities that happen to be near each other, Piplod does NOT belong on "
            "Vesu's line just for being nearby. When genuinely unsure whether something is a "
            "sub-part of a selected area or a separate neighboring area in its own right, "
            "leave it out of area_knowledge entirely — STEP B's default (see point 4) already "
            "protects a real listing from being lost by this omission.",
            "",
            "STEP B — per property, using what you just recalled in area_knowledge, decide "
            "in_service_area:",
            "",
            "1. If area_name already names one of the client-selected areas (allow for minor "
            "spelling/casing differences), set in_service_area TRUE.",
            "",
            "2. Otherwise, check the property's area_name/address against EVERY line of "
            "area_knowledge you wrote in STEP A — not against the bare area names again. If "
            "any recalled road/landmark/micro-locality for a selected area matches, set "
            "in_service_area TRUE for that area. This is exactly how a real VIP Road listing "
            "gets correctly matched to Vesu: because your own STEP A recall already said VIP "
            "Road belongs to Vesu, not because \"VIP Road\" and \"Vesu\" look alike as strings. "
            "Being geographically near/next to/adjoining a selected area is NOT the same as "
            "being a part of it — never set TRUE on proximity alone; it must be an actual "
            "match against a line you recalled in STEP A.",
            "",
            "3. If step 2 finds a match and area_name was null/didn't already name that "
            "selected area, SET area_name to that selected area's name (e.g. area_name "
            "becomes \"Vesu\") while leaving the original road/landmark text in address so "
            "no detail is lost — do not overwrite address with it, and do not remove it.",
            "",
            "4. Set in_service_area FALSE only when, after genuinely checking against every "
            "line of area_knowledge, you're reasonably confident the property is in a Surat "
            "locality that is distinct from every client-selected area. Missing everyone's "
            "benefit of the doubt here costs far more than the reverse: a real property in a "
            "selected area that gets wrongly marked FALSE is a lost client opportunity, while "
            "one wrongly marked TRUE is just an extra row someone skips past. So if you are "
            "genuinely unsure whether it belongs to a selected area or not, set in_service_area "
            "TRUE.",
            "",
            "5. area_match_reason and in_service_area are REQUIRED for EVERY property, with NO "
            "exceptions — a message with several properties needs a separate STEP B judgment "
            "for each one, even when two properties share a similar or identical location; "
            "never leave either field out for any property, and never let one property's "
            "presence make you skip judging another. area_match_reason is written BEFORE "
            "in_service_area — decide the reason first, then the verdict follows from it. It "
            "must name THAT property's own area_name/address and cite the specific fact from "
            "area_knowledge that decided it (e.g. \"VIP Road (this property's address) is "
            "listed under Vesu in area_knowledge\") — never the bare area name alone, and never "
            "a different property's location or reasoning copied across.",
            "",
            "6. If the client has selected no areas at all (list says \"(none configured)\"), "
            "set in_service_area TRUE for everything and area_knowledge can be null — there is "
            "nothing to recall or filter against.",
            "",
            "=== OUTPUT FORMAT ===",
            "",
            "Return ONLY a single JSON object — no markdown code fences, no commentary "
            "before or after it — with exactly this shape:",
            "",
            "{",
            "  \"area_knowledge\": \"<one line per client-selected area, per STEP A — or null "
            "if none are configured>\",",
            "  \"extractions\": [",
            "    {",
            "      \"source_message_id\": \"<copied exactly from the message's id>\",",
            "      \"is_property_listing\": true or false,",
            "      \"skip_reason\": \"<short reason, or null if is_property_listing is true>\",",
            "      \"properties\": [",
            "        {\"property_type\": string|null, \"bhk\": string|null, "
            "\"society_name\": string|null, \"area_name\": string|null, "
            "\"address\": string|null, \"carpet_area_sqft\": number|null, "
            "\"price_text\": string|null, \"price_amount_inr\": number|null, "
            "\"contact_name\": string|null, \"contact_phone\": string|null, "
            "\"description\": string|null, \"area_match_reason\": string, "
            "\"in_service_area\": true or false}",
            "      ]",
            "    }",
            "  ]",
            "}",
            "",
            "Field order inside each property object matters: write area_match_reason BEFORE "
            "in_service_area, exactly as shown above, so the reason is decided before the "
            "verdict rather than justified after it.",
            "",
            "\"extractions\" must contain exactly one object per input message, in the same "
            "order they were given, with \"source_message_id\" matching each message's id "
            "exactly. \"properties\" is an empty list whenever is_property_listing is false.",
        ]
    )


def _build_user_prompt(batch: List[WhatsAppChatMessage]) -> str:
    tracked_areas = area_filter_service.get_area_keywords()
    tracked_areas_list = ", ".join(tracked_areas) if tracked_areas else "(none configured)"
    lines = [
        f"Client-selected areas: {tracked_areas_list}.",
        "",
        "This list is NOT a signal for is_property_listing (PART 1) — judge that purely on "
        "whether it's a genuine listing/request, regardless of area. It IS what you match "
        "each property against for in_service_area (PART 3).",
        "",
        "Messages:",
    ]
    for message in batch:
        text = message.text.replace("\n", " ").strip()
        lines.append(f'- id={message.message_id} | group="{message.chat_name}" | text: "{text}"')
    return "\n".join(lines)


def _parse_extractions(response: httpx.Response, batch_size: int) -> List[GLMPropertyExtraction]:
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        step_logger.error(f"GLM returned an unexpected response shape for a batch of {batch_size}: {exc!r}")
        return []

    if not content:
        step_logger.error(f"GLM returned no usable content for a batch of {batch_size}.")
        return []

    content = _CODE_FENCE_RE.sub("", content.strip()).strip()

    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        step_logger.error(f"GLM response was not valid JSON for a batch of {batch_size}: {exc}")
        return []

    try:
        parsed = GLMExtractionResponse.model_validate(raw)
    except ValidationError as exc:
        step_logger.error(f"GLM response didn't match the expected schema for a batch of {batch_size}: {exc}")
        return []

    if parsed.area_knowledge:
        # Not parsed or matched against programmatically — purely the model's
        # own STEP A recall, surfaced so a human can audit *why* it made the
        # in_service_area calls it did for this batch (see glm_extraction_schema.py).
        step_logger.info(f"GLM's recalled area knowledge for this batch: {parsed.area_knowledge}")

    return parsed.extractions


def _merge_with_message_data(
    extractions: List[GLMPropertyExtraction], batch: List[WhatsAppChatMessage]
) -> List[StructuredProperty]:
    messages_by_id = {message.message_id: message for message in batch}
    seen_ids = set()
    properties: List[StructuredProperty] = []

    for extraction in extractions:
        message = messages_by_id.get(extraction.source_message_id)
        if message is None:
            step_logger.warn(
                "GLM returned an extraction for an unknown message id "
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
            # Logged for every property, not just outsiders: this is the only
            # visibility into whether in_service_area reflects genuine
            # per-property reasoning or a silently defaulted/omitted field
            # (GLMPropertyListing.in_service_area fails open to True) — worth
            # knowing either way, for auditing and for catching a model that
            # skips PART 3 reasoning on some properties in a multi-property
            # message.
            verdict = "in service area" if listing.in_service_area else "OUTSIDER"
            step_logger.info(
                f"Property from message {message.message_id!r} (area={listing.area_name!r}) -> {verdict}: "
                f"{listing.area_match_reason or 'no reason given by GLM'}"
            )
            properties.append(_to_structured_property(listing, message))

    for missing_id in set(messages_by_id) - seen_ids:
        step_logger.warn(f"GLM did not return anything for message id {missing_id!r} — dropped from this batch.")

    return properties


def _to_structured_property(listing: GLMPropertyListing, message: WhatsAppChatMessage) -> StructuredProperty:
    """Builds one StructuredProperty from one extracted listing, merged with
    the WhatsApp metadata shared by every listing pulled from that same
    message. Two listings from one message become two fully independent
    StructuredProperty records here — each is embedded and duplicate-checked
    on its own downstream (see property_pipeline_service.handle_batch_ready),
    exactly as if they had arrived in separate messages.

    A listing the LLM decided is outside every client-selected area
    (PART 3 of the prompt — in_service_area) is never dropped here: it is
    still stored, just flagged review_status="outsider" with the reason so
    it surfaces in the Outsider tab instead of silently vanishing — losing
    a wanted property is the one thing this pipeline must never do, and a
    wrongly-flagged outsider is still fully visible and correctable."""
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
        review_status="accepted" if listing.in_service_area else "outsider",
        review_notes=(
            None
            if listing.in_service_area
            else (listing.area_match_reason or "Outside the client's selected areas.")
        ),
        group_name=message.chat_name,
        chat_type=message.chat_type,
        sender_name=message.sender_name,
        sender_saved_name=message.sender_saved_name,
        sender_phone=message.sender_phone,
        message_text=message.text,
        message_timestamp=message.received_at,
    )
