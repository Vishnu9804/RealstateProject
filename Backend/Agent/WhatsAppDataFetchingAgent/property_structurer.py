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
            "BULK LISTINGS — a broker frequently sends a single message that bullet-lists ten "
            "or more separate, unrelated properties at once (a mix of plots/flats/bungalows in "
            "several different, unrelated localities, each its own bullet/line with its own "
            "size and price). This is normal, not an edge case: one bullet/line = one property, "
            "always, no matter how many other bullets are in the same message. Extract every "
            "single one, keep each one's own details (society/area/address/size/price) "
            "strictly separate from every other bullet's, and treat every one as fully "
            "independent of its neighbors in the list — two bullets being adjacent, similarly "
            "formatted, or sharing a price unit (e.g. both \"per Vaar\") does not make them "
            "related. Never merge two bullets into one property, and never split one bullet's "
            "own size/price/location details into more than one property entry.",
            "",
            "Keep society_name (a specific named building/project/society, e.g. \"Black "
            "Residency\") and area_name (the general locality, e.g. \"Althan\") strictly "
            "separate — do not put a locality in society_name or a building name in "
            "area_name. Only fill carpet_area_sqft if an explicit area number is stated in "
            "the message; never estimate it from the BHK.",
            "",
            "AREA UNIT — carpet_area_sqft takes the area number regardless of which unit it "
            "is written in: square feet (\"1200 sqft\", \"1200 sq ft\"), Vaar/Gaj (\"500 "
            "vaar\", \"500 gaj\"), or Vigha (\"2 vigha\"). Copy the bare number as written for "
            "whichever of these units appears — do NOT convert between units and do NOT "
            "guess a unit that isn't stated. carpet_area_unit records WHICH of those units it "
            "was — set it to exactly \"sqft\", \"vaar\", or \"vigha\" every time carpet_area_sqft "
            "is filled (never leave it null when carpet_area_sqft is set, and never set it when "
            "carpet_area_sqft is null). The dashboard shows this unit next to the number exactly "
            "as you set it (e.g. \"155 vaar\"), so getting it right matters as much as the number "
            "itself.",
            "",
            "PRICE FIELDS — there are two independent kinds of price, and they must never be "
            "confused with each other:",
            "  - price_text / price_amount_inr is the TOTAL price of the property.",
            "  - price_per_unit_text / price_per_unit_amount_inr is a PER-UNIT RATE — only "
            "fill this when the message explicitly states a rate per sq ft / per vaar / per "
            "vigha / per unit, phrased like \"1L per sq ft\", \"1L/sq ft\", \"85000/vaar\", "
            "\"2500 per sqft\", \"1.2cr/vigha\". A bare total price (e.g. \"85 Lakh\" with no "
            "\"per\"/\"/\" unit wording) is NEVER a per-unit rate, even if an area is also "
            "mentioned elsewhere in the same message — leave price_per_unit_text/"
            "price_per_unit_amount_inr null in that case. Likewise, do not copy a per-unit "
            "rate into price_text/price_amount_inr.",
            "",
            "PRICE FORMAT — normalize BOTH price_text and price_per_unit_text into compact "
            "Indian short-scale notation: \"cr\" for crore, \"L\" for lakh, \"k\" for thousand. "
            "Strip the ₹ symbol, \"Rs.\"/\"INR\", and Indian comma-grouping — e.g. "
            "\"1,25,00,000₹\" or \"1.25 crore\" becomes \"1.25cr\"; \"Rs.45,00,000/-\" or "
            "\"45 Lakh\" becomes \"45L\"; \"15,000/month\" becomes \"15k/month\"; a per-unit "
            "\"1,25,000/vaar\" becomes \"1.25L/vaar\". Keep any \"/vaar\", \"/vigha\", "
            "\"/sq ft\", \"/month\" unit suffix from the original wording on price_per_unit_text "
            "(and on price_text only when the message itself qualified the total that way, e.g. "
            "\"/month\" rent). This formatting must be exact and consistent for every price you "
            "extract — get it right every time, not just usually.",
            "",
            "Never invent details that are not present in the message text. If a field is "
            "not mentioned, use null rather than guessing. Do not perform any arithmetic "
            "yourself (no dividing a total by an area, no multiplying a rate by an area) — "
            "only transcribe and reformat numbers that are actually written in the message; "
            "any derived value is computed deterministically after extraction, not by you.",
            "",
            "RENT VS SALE CLASSIFICATION (listing_type) — every extracted property must be "
            "classified as exactly one of \"Sale\" or \"Rent\". This is just as important as PART 1's "
            "is_property_listing call and PART 3's area matching — never skip it or fill it in on "
            "autopilot. These messages are written in a mix of English, Hindi, and Gujarati (often "
            "transliterated into Latin script with inconsistent spelling), so recognize the intent "
            "behind the wording, not just exact keywords:",
            "  - RENT signals: \"rent\", \"for rent\", \"on rent\", \"rent par\", \"to let\", \"lease\", "
            "\"bhade\", \"bhade pe\", \"bhade pe dena hai\", \"bhadu\", \"bhada\", \"bhada pr\", \"bahde\", "
            "\"bhade apvanu che/chhe\", \"bahde apvanu chhe\", and other spelling variants of the same "
            "Hindi/Gujarati words for \"rent\" (spelling varies a lot writer-to-writer — match the "
            "sound/intent, not one exact spelling).",
            "  - SALE signals: \"sale\", \"for sale\", \"to sell\", \"bechna hai\", \"vechvanu che/chhe\", "
            "\"vechvani che\", \"vecvu che\", and other spelling variants of the same Hindi/Gujarati words "
            "for \"sell\"/\"for sale\".",
            "  - REQUIRED FIRST STEP — before deciding listing_type, actually re-read this property's "
            "own message text specifically looking for the RENT and SALE signal words above. Write what "
            "you found (or that you found nothing) into listing_type_reason BEFORE writing listing_type "
            "— exactly the same reason-before-verdict discipline as area_match_reason/in_service_area in "
            "PART 3. Do not write listing_type first and rationalize a reason afterward.",
            "  - If the message contains a clear RENT signal for that property, set listing_type to "
            "\"Rent\" — regardless of property_type (even a Plot/Land can genuinely be offered for "
            "rent, though this is rare). A RENT signal ALWAYS overrides the \"Sale\" default — never let "
            "the property being a Flat/Shop/Office/Bungalow (types normally sold) pull you back toward "
            "\"Sale\" once you've actually found rent wording.",
            "  - If the message contains a clear SALE signal, or if it mentions BOTH and the SALE "
            "signal is the one that actually applies to this specific property, set listing_type to "
            "\"Sale\".",
            "  - EDGE CASE — property types that are almost always sold outright (Plot/Land, and "
            "similar) but where the message gives NO explicit Rent/Sale wording at all: default to "
            "\"Sale\". Do not infer \"Rent\" just because a price is quoted, an area is quoted, or "
            "monthly-sounding numbers appear — only an actual rent signal as above justifies \"Rent\".",
            "  - DEFAULT — whenever the message gives no explicit Sale or Rent signal at all for a "
            "property (of any property_type), set listing_type to \"Sale\". Never leave listing_type "
            "null and never guess \"Rent\" without a genuine explicit signal — \"Rent\" must always be "
            "earned by clear wording in the message, while \"Sale\" is the safe default otherwise.",
            "  - In a multi-property message, judge listing_type separately for each property from "
            "that property's own wording — the same rule as area matching in PART 3: never borrow one "
            "property's Sale/Rent signal for a different property in the same message.",
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
            "3. CRITICAL — NEVER borrow a different property's location. A real broker message "
            "very often lists MANY separate, unrelated properties in one text — a batch of plot "
            "listings all priced \"per Vaar\", say, where most are in one locality and one or "
            "two are somewhere completely different. Each property's in_service_area decision "
            "uses ONLY that property's OWN area_name/address as extracted for IT in PART 2 — "
            "never a different property's area_name/address, even when that other property is "
            "the very next line in the list, shares the same formatting/pricing style, or sits "
            "under the same heading. A locality that appears only in a DIFFERENT property's "
            "line is not evidence for this property — treat it as if it were not in the message "
            "at all. CONCRETE FAILURE TO AVOID: a message lists a plot in \"Vadod\" and, "
            "separately, a plot on \"VIP Road\" (which area_knowledge says is part of Vesu). It "
            "is WRONG to write the Vadod property's area_match_reason as \"listed under VIP "
            "Road, part of Vesu\" — VIP Road is the OTHER property's address, not Vadod's; "
            "Vadod itself was correctly recognized as not matching anything in area_knowledge, "
            "so in_service_area for that Vadod property must be FALSE. Getting the right "
            "locality for a property and then matching a DIFFERENT locality's result onto it is "
            "a pure bookkeeping error, not a defensible use of the fail-open default in point 5 "
            "below — that default is for genuine uncertainty about ONE property's own location, "
            "never for carrying a correct-but-unrelated verdict over from another property.",
            "",
            "4. If point 2 finds a match and area_name was null/didn't already name that "
            "selected area, SET area_name to that selected area's name (e.g. area_name "
            "becomes \"Vesu\") while leaving the original road/landmark text in address so "
            "no detail is lost — do not overwrite address with it, and do not remove it.",
            "",
            "5. Set in_service_area FALSE only when, after genuinely checking THIS property's "
            "own area_name/address against every line of area_knowledge, you're reasonably "
            "confident it is in a Surat locality distinct from every client-selected area. "
            "Missing everyone's benefit of the doubt here costs far more than the reverse: a "
            "real property in a selected area that gets wrongly marked FALSE is a lost client "
            "opportunity, while one wrongly marked TRUE is just an extra row someone skips "
            "past. So when you are genuinely unsure whether THIS property's OWN location "
            "belongs to a selected area, set in_service_area TRUE — but this default is never "
            "a license to reuse a different property's area/verdict (see point 3).",
            "",
            "6. area_match_reason and in_service_area are REQUIRED for EVERY property, with NO "
            "exceptions — a message with several properties needs a separate STEP B judgment "
            "for each one, even when two properties share a similar or identical location; "
            "never leave either field out for any property, and never let one property's "
            "presence make you skip judging another. area_match_reason is written BEFORE "
            "in_service_area — decide the reason first, then the verdict follows from it. It "
            "must name THAT property's own area_name/address and cite the specific fact from "
            "area_knowledge that decided it (e.g. \"VIP Road (this property's address) is "
            "listed under Vesu in area_knowledge\") — never the bare area name alone, and never "
            "a different property's location or reasoning copied across (see point 3).",
            "",
            "7. If the client has selected no areas at all (list says \"(none configured)\"), "
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
            "\"carpet_area_unit\": \"sqft\"|\"vaar\"|\"vigha\"|null, "
            "\"price_text\": string|null, \"price_amount_inr\": number|null, "
            "\"price_per_unit_text\": string|null, \"price_per_unit_amount_inr\": number|null, "
            "\"listing_type_reason\": string, \"listing_type\": \"Sale\"|\"Rent\", "
            "\"contact_name\": string|null, \"contact_phone\": string|null, "
            "\"description\": string|null, \"area_match_reason\": string, "
            "\"in_service_area\": true or false}",
            "      ]",
            "    }",
            "  ]",
            "}",
            "",
            "Field order inside each property object matters: write listing_type_reason BEFORE "
            "listing_type, and area_match_reason BEFORE in_service_area, exactly as shown above, so "
            "each reason is decided before its verdict rather than justified after it.",
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
            # Same auditing rationale as the area_match_reason log above —
            # this is the only visibility into whether listing_type reflects
            # genuine per-property reasoning or a silently defaulted/omitted
            # field (GLMPropertyListing.listing_type fails open to "Sale").
            step_logger.info(
                f"Property from message {message.message_id!r} -> listing_type={listing.listing_type!r}: "
                f"{listing.listing_type_reason or 'no reason given by GLM'}"
            )
            properties.append(
                _to_structured_property(listing, message, single_property_message=len(extraction.properties) == 1)
            )

    for missing_id in set(messages_by_id) - seen_ids:
        step_logger.warn(f"GLM did not return anything for message id {missing_id!r} — dropped from this batch.")

    return properties


def _to_structured_property(
    listing: GLMPropertyListing, message: WhatsAppChatMessage, single_property_message: bool
) -> StructuredProperty:
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
    wrongly-flagged outsider is still fully visible and correctable.

    single_property_message tells _sanitize_listing_type whether
    message.text can safely be scanned as evidence for THIS property alone
    (true only when the message held exactly one property) — see that
    function's docstring for why a multi-property message can't use the
    same keyword-search shortcut."""
    structured = StructuredProperty(
        source_message_id=message.message_id,
        property_type=listing.property_type,
        bhk=listing.bhk,
        society_name=listing.society_name,
        area_name=listing.area_name,
        address=listing.address,
        carpet_area_sqft=listing.carpet_area_sqft,
        carpet_area_unit=listing.carpet_area_unit,
        price_text=listing.price_text,
        price_amount_inr=listing.price_amount_inr,
        price_per_unit_text=listing.price_per_unit_text,
        price_per_unit_amount_inr=listing.price_per_unit_amount_inr,
        listing_type=listing.listing_type,
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
    _sanitize_and_parse_prices(structured)
    _fill_missing_price_or_area(structured)
    if single_property_message:
        _sanitize_listing_type(structured)
    return structured


_CRORE = 10_000_000
_LAKH = 100_000
_THOUSAND = 1_000

# Catches a per-unit rate the LLM mislabeled as the total price (e.g.
# price_text ends up holding "1.25L per Vaar" instead of a real total) —
# despite the PRICE FIELDS prompt rule telling it never to do this, a small
# fast model still occasionally does, and this pipeline promised 100%
# accuracy on the total-vs-per-unit distinction, not "usually right".
_PER_UNIT_HINT_RE = re.compile(
    r"(?:per\s*(?:sq\.?\s*ft|sqft|vaar|gaj|vigha|yard|unit)|/\s*(?:sq\.?\s*ft|sqft|vaar|gaj|vigha|yard))",
    re.IGNORECASE,
)

# Same rationale as _PER_UNIT_HINT_RE above: never trust the LLM's Sale-vs-
# Rent call as the only line of defense. GLM-4.7-FlashX occasionally leaves
# listing_type at its "Sale" default even when the message plainly contains
# an explicit rent word (e.g. "Bhade pe Dena hai") — this deterministic word
# list catches the unambiguous cases the model misses. Only additive toward
# "Rent": it never overrides an LLM call of "Rent" back to "Sale", since a
# stray "sale" substring elsewhere in a message is far less reliable
# evidence than an explicit rent word is for "Rent".
_RENT_KEYWORDS = (
    "rent", "on rent", "for rent", "rent par", "rent pe", "to let", "lease",
    "bhade", "bhada", "bhadu", "bhado", "bhadey", "bahde", "bahda", "bahdu",
)
_SALE_KEYWORDS = (
    "sale", "for sale", "to sell", "resale",
    "bechna", "bechvu", "bechvanu", "bechvani",
    "vechvanu", "vechvani", "vechay", "vecvu", "vechvu",
)
_RENT_SIGNAL_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(word) for word in _RENT_KEYWORDS) + r")\b", re.IGNORECASE
)
_SALE_SIGNAL_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(word) for word in _SALE_KEYWORDS) + r")\b", re.IGNORECASE
)


def _sanitize_listing_type(prop: StructuredProperty) -> None:
    """Deterministic safety net over the LLM's Sale/Rent classification, run
    only when the source message held exactly ONE property (see
    _to_structured_property's single_property_message) — for a multi-
    property/bulk-listing message, message_text covers every property in
    it, so a rent word anywhere in the text could belong to a completely
    different bullet/line and wrongly flip an unrelated Sale property (the
    same "never borrow a different property's signal" problem the area-
    matching rules guard against); the LLM's own per-property reasoning is
    the only safe source of truth there.

    For the single-property case, forces "Rent" whenever the message
    contains a clear rent keyword and no sale keyword — never the reverse
    (an LLM call of "Rent" is left untouched, and a message containing both
    words is left to the LLM's own judgement rather than guessed at here)."""
    if prop.listing_type == "Rent":
        return
    text = prop.message_text or ""
    if _RENT_SIGNAL_RE.search(text) and not _SALE_SIGNAL_RE.search(text):
        prop.listing_type = "Rent"

# Finds the first number(+scale) in a price string, ignoring any trailing
# "per vaar"/"/sqft" wording — used as a deterministic fallback whenever the
# LLM left an *_amount_inr null but its own *_text is plainly parseable, so
# the area/rate/total derivation below isn't at the mercy of the LLM
# remembering to also fill the numeric field every time.
_PRICE_CLEAN_RE = re.compile(r"[₹,]|rs\.?|inr", re.IGNORECASE)
_PRICE_NUMBER_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(cr|crore|crores|l|lac|lacs|lakh|lakhs|k|thousand)?", re.IGNORECASE
)
_SCALE_MULTIPLIERS = {
    "cr": _CRORE,
    "crore": _CRORE,
    "crores": _CRORE,
    "l": _LAKH,
    "lac": _LAKH,
    "lacs": _LAKH,
    "lakh": _LAKH,
    "lakhs": _LAKH,
    "k": _THOUSAND,
    "thousand": _THOUSAND,
}


def _parse_price_text_to_inr(text: str) -> Optional[float]:
    cleaned = _PRICE_CLEAN_RE.sub("", text)
    match = _PRICE_NUMBER_RE.search(cleaned)
    if not match:
        return None
    value = float(match.group(1))
    scale = (match.group(2) or "").lower()
    return value * _SCALE_MULTIPLIERS[scale] if scale else value


def _sanitize_and_parse_prices(prop: StructuredProperty) -> None:
    """Deterministic safety net over the LLM's PRICE FIELDS separation, run
    right after structuring — never trusts the LLM's total-vs-per-unit split
    (or its numeric parsing) as the only line of defense:

    1. If price_text itself reads like a per-unit rate ("...per vaar",
       ".../sq ft"), the LLM mislabeled a rate as the total. Reclassify it
       as the per-unit rate (unless that field is already filled) and clear
       price_text/price_amount_inr — a "total" that is actually a rate is
       strictly worse than leaving Price blank, since a blank total still
       lets _fill_missing_price_or_area derive the real one from
       area * rate.
    2. Whenever an *_amount_inr is null but its matching *_text is present,
       try to parse a plain number out of the text — the LLM sometimes
       writes a clean, parseable price string without also filling the
       numeric field, which would otherwise silently block the area/rate/
       total derivation from having the two inputs it needs.
    """
    if _PER_UNIT_HINT_RE.search(prop.price_text or ""):
        if not prop.price_per_unit_text:
            prop.price_per_unit_text = prop.price_text
        prop.price_text = None
        prop.price_amount_inr = None

    if prop.price_amount_inr is None and prop.price_text:
        prop.price_amount_inr = _parse_price_text_to_inr(prop.price_text)
    if prop.price_per_unit_amount_inr is None and prop.price_per_unit_text:
        prop.price_per_unit_amount_inr = _parse_price_text_to_inr(prop.price_per_unit_text)


def _format_compact_inr(amount: float) -> str:
    """Mirrors Frontend/src/lib/formatters.ts's formatCompactInr, so a value
    this module derives (rather than the LLM) reads identically to one the
    LLM formatted itself or the frontend would format from a raw number —
    up to two decimals, trailing zeros dropped."""
    magnitude = abs(amount)
    if magnitude >= _CRORE:
        return f"{_trim_number(amount / _CRORE)}cr"
    if magnitude >= _LAKH:
        return f"{_trim_number(amount / _LAKH)}L"
    if magnitude >= _THOUSAND:
        return f"{_trim_number(amount / _THOUSAND)}k"
    return _trim_number(amount)


def _trim_number(value: float) -> str:
    rounded = round(value, 2)
    return f"{rounded:g}"


def _fill_missing_price_or_area(prop: StructuredProperty) -> None:
    """Deterministic post-processing, run after the LLM has structured the
    property — never inside the LLM prompt itself. carpet_area_sqft,
    price_amount_inr and price_per_unit_amount_inr are three numbers that
    are only ever dimensionally consistent within one listing (all in
    whatever single unit — sqft/vaar/vigha — that listing used), so
    "total = area * rate" holds exactly as written, with no unit
    conversion. When exactly one of the three is missing and the other two
    are present, the third is computed here; when two or more are missing
    there isn't enough information to derive anything, so every field is
    left exactly as the LLM returned it (null stays null, shown as "—" in
    the UI, same as today)."""
    area = prop.carpet_area_sqft
    total = prop.price_amount_inr
    per_unit = prop.price_per_unit_amount_inr

    present = sum(value is not None for value in (area, total, per_unit))
    if present != 2:
        return

    if total is None:
        if per_unit == 0:
            return
        total = area * per_unit
        prop.price_amount_inr = total
        if not prop.price_text:
            prop.price_text = _format_compact_inr(total)
    elif area is None:
        if per_unit is None or per_unit == 0:
            return
        area = total / per_unit
        prop.carpet_area_sqft = area
    elif per_unit is None:
        if area == 0:
            return
        per_unit = total / area
        prop.price_per_unit_amount_inr = per_unit
        if not prop.price_per_unit_text:
            suffix = f"/{prop.carpet_area_unit}" if prop.carpet_area_unit else ""
            prop.price_per_unit_text = f"{_format_compact_inr(per_unit)}{suffix}"
