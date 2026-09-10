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
import math
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
# returns, so the retry budget is the only thing standing between a Z.ai blip
# and permanently lost listings.
#
# Measured directly against the API while diagnosing the repeated timeouts:
# Z.ai's latency for this workload is not merely slow, it is wildly variable
# and periodically bad — the identical request completed in ~60s at one point
# and, an hour later, returned nothing at all within 240s on a freshly-opened
# connection, and one attempt came back HTTP 500. Three attempts was tuned for
# "a network hiccup"; a fourth (with a longer final backoff, giving a bad
# patch time to pass) is what an outage-shaped failure actually needs. This
# only ever fires on failure, so it costs nothing on the calls that succeed
# first time — which, now that the reply is streamed, is the overwhelming
# majority.
_MAX_ATTEMPTS = 4
_RETRY_BACKOFF_SECONDS = [3.0, 10.0, 30.0]

# Absolute wall-clock ceiling for one streamed response. The per-chunk
# `read` timeout above already catches a dead connection, but it can never
# catch a stream that keeps dribbling tokens forever — this does. Set far
# above any real generation (a full 10-message batch streams in well under
# two minutes) so it only ever fires on genuinely pathological output.
_MAX_STREAM_SECONDS = 600.0


# STEP A's area recall (see _build_system_prompt) is expensive and, by the
# prompt's own definition, NOT message-specific: it is general knowledge
# about the client's selected areas, identical for every batch until that
# area list changes. Regenerating it on every request meant paying for those
# output tokens ~50 times a day at 500 messages/day, and paying MORE the more
# areas a client selects — precisely the wrong scaling for a settings page
# users are meant to fill in.
#
# So it is generated once and reused: the model's own STEP A output is kept
# here, keyed by the exact area list that produced it, and handed back to it
# as already-done work on subsequent batches. Any change to the selected
# areas misses the key and regenerates it once, so the knowledge can never go
# stale relative to the settings. In memory only — a process restart just
# pays for one regeneration, which is not worth a database round-trip.
_area_knowledge_cache: dict = {}


def _area_cache_key(areas: List[str]) -> tuple:
    return tuple(areas)


def _get_cached_area_knowledge(areas: List[str]) -> Optional[str]:
    cached = _area_knowledge_cache.get(_area_cache_key(areas))
    # A stub/empty recall is not worth reusing — better to let the model
    # redo STEP A properly than to lock in a useless line.
    return cached if cached and len(cached) > 50 else None


def _remember_area_knowledge(areas: List[str], knowledge: Optional[str]) -> None:
    if not knowledge or len(knowledge) <= 50:
        return
    # Never cache a looped recall: it would otherwise be pinned in front of
    # every subsequent batch, turning one bad generation into a permanent
    # source of corrupt area matching.
    if _looks_like_repetition_loop(knowledge):
        step_logger.warn(
            "GLM's area knowledge came back with a repetition loop in it — not caching it, so "
            "the next batch recalls it cleanly instead of reusing corrupt knowledge."
        )
        return
    key = _area_cache_key(areas)
    if key in _area_knowledge_cache:
        return
    _area_knowledge_cache.clear()  # only ever one area list is current
    _area_knowledge_cache[key] = knowledge
    step_logger.info(
        f"Cached GLM's area knowledge for the current {len(areas)} selected area(s) — later batches "
        "reuse it instead of re-deriving it every time, which is both faster and cheaper. It is "
        "regenerated automatically if the selected areas change."
    )


def _looks_like_repetition_loop(text: str) -> bool:
    """True when the tail of `text` is one short phrase repeated over and
    over — a model stuck in a degenerate generation loop.

    Detects the real observed failure: GLM emitting "Vesu Garden, " ~600
    times into area_knowledge. Deliberately conservative, because a false
    positive throws away a real extraction: the repeating unit must fill at
    least _LOOP_MIN_CHARS of contiguous tail AND repeat at least
    _LOOP_MIN_REPEATS times. Ordinary output never does this — even a long
    list of similar properties differs in size/price/address every entry."""
    tail = text[-_LOOP_WINDOW_CHARS:]
    if len(tail) < _LOOP_MIN_CHARS:
        return False
    for unit_length in range(3, 81):
        unit = tail[-unit_length:]
        if not unit.strip():
            continue
        repeats = 1
        while (
            len(tail) >= unit_length * (repeats + 1)
            and tail[-unit_length * (repeats + 1) : -unit_length * repeats] == unit
        ):
            repeats += 1
        if repeats >= _LOOP_MIN_REPEATS and repeats * unit_length >= _LOOP_MIN_CHARS:
            return True
    return False


# Tuning for _looks_like_repetition_loop. A loop has to fill 400+ contiguous
# characters with 6+ back-to-back copies of the same short unit before it is
# called one — well beyond anything legitimate output produces.
_LOOP_WINDOW_CHARS = 3000
_LOOP_MIN_CHARS = 400
_LOOP_MIN_REPEATS = 6

# Backstop for runaway generation that is NOT a short repeated phrase — a
# longer cycle, or the model simply never stopping. Observed emitting 49,833
# characters for a single 8-property message that parses to ~8,000 when it
# behaves, with no short repeating unit for the loop detector to find. A flat
# ceiling catches every runaway shape without having to characterise it: no
# legitimate batch of 10 messages comes anywhere near this, since even 100
# properties across the batch land around 25,000 characters.
_MAX_CONTENT_CHARS = 60_000
# How often to run the check while streaming. Every chunk would be wasteful;
# every 150 chunks catches a loop within a second or two of it starting,
# instead of letting it run out the full _MAX_STREAM_SECONDS budget.
_LOOP_CHECK_EVERY_CHUNKS = 150


class _TransientCompletionError(Exception):
    """A response that arrived but is unusable (empty, or a stream that ran
    past _MAX_STREAM_SECONDS). Worth exactly the same retry treatment as a
    dropped connection — unlike a 4xx, it may well succeed next time."""


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
            # Granular, not one blanket number, because these four waits
            # mean completely different things once the response is
            # STREAMED (see _stream_completion): `read` is no longer "how
            # long the whole answer may take to generate" but "how long a
            # silence between two tokens may last". A 45-90s generation
            # therefore never trips it — only a genuinely dead connection
            # does, which is precisely the distinction the old single
            # timeout=180.0 could not make.
            timeout=httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=15.0),
            # Batches arrive at most once a minute and often far less
            # often, so a pooled connection is usually cold by the time the
            # next one needs it. Z.ai (or any NAT/proxy in between) silently
            # drops those idle sockets, and reusing one costs a full `read`
            # timeout before httpx gives up — a hang, not a clean error.
            # Expiring them well under the inter-batch gap means the client
            # opens a fresh connection instead of betting on a stale one.
            limits=httpx.Limits(max_keepalive_connections=5, keepalive_expiry=30.0),
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

    content = _post_with_retries(batch)
    if content is None:
        return []

    extractions = _parse_extractions(content, len(batch))
    extractions = _recover_missed_properties(extractions, batch)
    return _merge_with_message_data(extractions, batch)


def _recover_missed_properties(
    extractions: List[GLMPropertyExtraction], batch: List[WhatsAppChatMessage]
) -> List[GLMPropertyExtraction]:
    """Deterministic guard over PART 2's bulk-listing extraction, in the same
    spirit as _sanitize_listing_type and _verify_total_price_against_text:
    never trust the LLM's own count as the only line of defense.

    The model is required to inventory every property line verbatim into
    property_lines BEFORE extracting (PART 2, STEP 0), so a shortfall between
    the two lists is the model contradicting itself — it enumerated N
    properties and then returned fewer, i.e. it silently dropped real
    listings (observed: an 8-bullet shop listing stored as 5). That is
    exactly the failure this pipeline must never have, so those messages get
    ONE corrective re-ask that quotes back the lines it dropped.

    The re-ask covers only the affected messages, and only ever ADDS: a
    message is replaced solely when the retry returns more properties than
    the first attempt, so a worse or equal second answer can never lose data
    the first one already had. Costs nothing on the overwhelming majority of
    batches, where every message's two lists already agree and this returns
    immediately."""
    messages_by_id = {message.message_id: message for message in batch}
    shortfalls = {
        extraction.source_message_id: extraction
        for extraction in extractions
        if extraction.is_property_listing
        and extraction.source_message_id in messages_by_id
        and len(extraction.property_lines) > len(extraction.properties)
    }
    if not shortfalls:
        return extractions

    for extraction in shortfalls.values():
        step_logger.warn(
            f"GLM listed {len(extraction.property_lines)} property line(s) for message "
            f"{extraction.source_message_id!r} but only returned {len(extraction.properties)} "
            "structured propert(ies) — it dropped listings it had itself counted. Re-asking for "
            "just this message rather than storing an incomplete listing."
        )

    retry_batch = [messages_by_id[message_id] for message_id in shortfalls]
    correction_lines = [
        "CORRECTION — a previous attempt at these exact messages returned FEWER properties than "
        "the property lines it had itself inventoried, silently dropping real listings. For each "
        "message below, it counted these property lines:",
    ]
    for message_id, extraction in shortfalls.items():
        correction_lines.append(f"  message {message_id} — {len(extraction.property_lines)} property line(s):")
        correction_lines.extend(f"    - {line}" for line in extraction.property_lines)
        correction_lines.append(
            f"    ...but returned only {len(extraction.properties)} propert(ies). Every one of those "
            "lines is a real, separate property and MUST get its own entry this time."
        )
    correction_lines.append(
        "Redo the extraction for these messages in full, following PART 2 STEP 0 and the NEVER DROP "
        "A LINE rules exactly: \"properties\" must have one entry per line above, same count, same "
        "order. A line whose numbers look odd, that resembles another line, or that sits in an "
        "unfamiliar locality still gets its own entry — extract it as written and let PART 3's "
        "in_service_area handle area relevance."
    )

    retry_content = _post_with_retries(retry_batch, correction="\n".join(correction_lines))
    if retry_content is None:
        step_logger.warn(
            f"Corrective re-ask failed for {len(retry_batch)} message(s) — keeping the original, "
            "possibly incomplete extraction rather than losing it entirely."
        )
        return extractions

    retried_by_id = {
        extraction.source_message_id: extraction
        for extraction in _parse_extractions(retry_content, len(retry_batch))
    }

    recovered: List[GLMPropertyExtraction] = []
    for extraction in extractions:
        retried = retried_by_id.get(extraction.source_message_id)
        if retried is not None and len(retried.properties) > len(extraction.properties):
            step_logger.info(
                f"Corrective re-ask recovered message {extraction.source_message_id!r}: "
                f"{len(extraction.properties)} -> {len(retried.properties)} propert(ies)."
            )
            recovered.append(retried)
        else:
            if extraction.source_message_id in shortfalls:
                step_logger.warn(
                    f"Corrective re-ask did not improve message {extraction.source_message_id!r} "
                    f"({len(extraction.properties)} propert(ies) for "
                    f"{len(extraction.property_lines)} listed line(s)) — storing what we have; "
                    "this message is worth a human look."
                )
            recovered.append(extraction)
    return recovered


def _stream_completion(client: httpx.Client, request_body: dict) -> str:
    """Sends one structuring request and assembles the streamed reply into
    the same single content string a non-streamed call would have returned.

    Streaming is here for timeout behaviour, not for progressive display:
    nothing downstream can use a partial JSON object, so the caller still
    waits for the whole thing. What changes is WHAT the client is waiting
    on. A non-streamed call is one long silence — the server sends nothing
    at all until the entire JSON reply is generated — so the read timeout
    has to cover full generation time, and a 45-90s answer sits
    indistinguishably close to a connection that has quietly died. Streamed,
    the first token arrives in a second or two and tokens keep coming, so
    the read timeout only ever measures the gap between chunks: a slow
    answer no longer looks like a dead one, and the observed "timed out
    twice at exactly 180s, then succeeded in 65s" pattern cannot recur.

    Raises _TransientCompletionError if the stream yields no content or runs
    past _MAX_STREAM_SECONDS — both retryable, unlike a 4xx."""
    started = time.monotonic()
    pieces: List[str] = []
    content_chars = 0
    with client.stream("POST", "chat/completions", json=request_body) as response:
        if response.status_code >= 400:
            # The body of a streamed error response hasn't been read yet;
            # read it first so raise_for_status's message carries Z.ai's
            # actual explanation rather than an empty stream.
            response.read()
            response.raise_for_status()
        for line in response.iter_lines():
            if time.monotonic() - started > _MAX_STREAM_SECONDS:
                raise _TransientCompletionError(
                    f"stream ran past {_MAX_STREAM_SECONDS:.0f}s without finishing"
                )
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if payload == "[DONE]":
                break
            try:
                delta = json.loads(payload)["choices"][0]["delta"]
            except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                # A keep-alive/usage-only frame, not a content chunk.
                continue
            piece = delta.get("content")
            if piece:
                pieces.append(piece)
                content_chars += len(piece)
                if content_chars > _MAX_CONTENT_CHARS:
                    raise _TransientCompletionError(
                        f"the model produced over {_MAX_CONTENT_CHARS:,} characters for this batch "
                        "— runaway generation, far beyond any real extraction"
                    )
                # Catch a degenerate repetition loop within a second or two
                # of it starting, rather than paying out the whole stream
                # budget for output that is guaranteed to be unusable.
                if len(pieces) % _LOOP_CHECK_EVERY_CHUNKS == 0 and _looks_like_repetition_loop(
                    "".join(pieces[-400:])
                ):
                    raise _TransientCompletionError(
                        "the model fell into a repetition loop (same phrase emitted over and "
                        "over) — abandoning this attempt instead of waiting out a reply that "
                        "cannot parse"
                    )

    content = "".join(pieces)
    if not content.strip():
        raise _TransientCompletionError("the stream completed without any content")
    return content


def _post_with_retries(
    batch: List[WhatsAppChatMessage], correction: Optional[str] = None
) -> Optional[str]:
    """Sends the structuring request and returns the model's raw reply text,
    retrying up to _MAX_ATTEMPTS times on transient failures only: a
    timeout/connection drop, an empty/overlong stream, or a 5xx/429 from
    Z.ai — never on a 4xx like bad auth or a malformed request, which will
    just fail identically (and cost identically) on every retry. Returns
    None only once every attempt has failed."""
    client = _get_client()
    request_body = {
        "model": get_settings().zai_model,
        "messages": [
            {
                "role": "system",
                "content": _build_system_prompt(
                    area_knowledge_supplied=_get_cached_area_knowledge(
                        area_filter_service.get_area_keywords()
                    )
                    is not None
                ),
            },
            {"role": "user", "content": _build_user_prompt(batch, correction)},
        ],
        # NOT 0.0, despite this being a pure extraction task where maximum
        # determinism sounds ideal. temperature=0.0 is greedy decoding, and
        # a small model doing greedy decoding is highly prone to degenerate
        # repetition: once it emits the same phrase twice, that phrase is
        # by construction the most likely continuation again, so it locks
        # into an infinite loop. Observed in production writing "Vesu
        # Garden, " roughly 600 times into area_knowledge before spilling
        # the message text into that field — which then corrupted the whole
        # extraction (a 1-property message came back as 2, an 8-property
        # message as 5) and burned the entire 600s stream budget.
        #
        # A small positive temperature gives the sampler just enough room to
        # step off that loop while keeping extraction effectively
        # repeatable. This is the fix for the runaway generations, the
        # 600s timeouts, and the wrong property counts — all three were the
        # same bug. Paired with the repetition guard in _stream_completion,
        # which aborts and retries if a loop starts anyway.
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        # Server-side ceiling on a runaway. The client-side guards in
        # _stream_completion stop US waiting, but only this stops Z.ai
        # GENERATING — and therefore stops us being billed for 50,000
        # characters of a loop that could never have parsed. Sized with
        # real headroom: a full 10-message batch carrying ~100 properties
        # lands near 7,000 tokens, so this bounds the pathological case
        # without ever truncating a legitimate reply.
        "max_tokens": 16000,
        # GLM-4.7-FlashX runs chain-of-thought reasoning by default,
        # which is built for open-ended/agentic tasks — it adds
        # significant latency for no benefit on a fixed-shape
        # extraction task like this one (it was the direct cause of
        # timeouts on batches with several properties in one
        # message) and is turned off here for speed.
        "thinking": {"type": "disabled"},
        # See _stream_completion: streamed purely so the read timeout
        # measures the gap between tokens instead of total generation time.
        "stream": True,
    }

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return _stream_completion(client, request_body)
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
        except (httpx.TimeoutException, httpx.TransportError, _TransientCompletionError) as exc:
            if attempt == _MAX_ATTEMPTS:
                step_logger.error(
                    f"GLM structuring request failed for a batch of {len(batch)} "
                    f"(attempt {attempt}/{_MAX_ATTEMPTS}): {exc!r}"
                )
                return None
            step_logger.warn(
                f"GLM structuring request timed out/dropped for a batch of {len(batch)} "
                f"(attempt {attempt}/{_MAX_ATTEMPTS}) — retrying rather than losing these "
                "properties. Now that the reply is streamed, this means the connection "
                "genuinely went quiet, not merely that generation took a while: "
                f"{exc!r}"
            )
        except Exception as exc:  # noqa: BLE001
            # Anything else (bad API key, DNS failure, ...) will fail the
            # exact same way on every retry — burning two more paid calls to
            # confirm that would just be wasted spend.
            step_logger.error(f"GLM structuring request failed for a batch of {len(batch)}: {exc!r}")
            return None

        time.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])

    return None


def _build_system_prompt(area_knowledge_supplied: bool = False) -> str:
    """When area_knowledge_supplied is True, STEP A's recall is already in
    the user prompt (from a previous batch — see _area_knowledge_cache) and
    the model is told to use it rather than write it again. STEP B, the part
    that actually judges each property, is completely unchanged either way."""
    step_a = (
        [
            "STEP A — ALREADY DONE FOR YOU. The area knowledge described below has already "
            "been recalled and is given to you in the user message under \"Area knowledge\". "
            "Use it exactly as if you had just written it yourself. Do NOT write it again: "
            "set the top-level \"area_knowledge\" field to null and spend your output on the "
            "extractions instead. It is the same general knowledge for every batch, so "
            "re-deriving it each time would only cost time.",
        ]
        if area_knowledge_supplied
        else [
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
            "HARD LIMITS for STEP A — keep it SHORT: at most 8 items for a given area, and at "
            "most one line per area. Never write the same item twice, never pad the line to "
            "make it look fuller, and never copy any text from the messages below into "
            "area_knowledge — it is general knowledge about the areas only. If you find "
            "yourself repeating an item, STOP that line immediately and move to the next area. "
            "Then write \"extractions\" — area_knowledge is a brief aid, not the answer, and "
            "an over-long one costs accuracy on the extractions that actually matter.",
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
        ]
    )
    return "\n".join(
        [
            "You are a real estate data-extraction assistant. You will be given a batch "
            "of raw WhatsApp messages from Indian real estate broker/community groups in "
            "Surat, India, each already confirmed by a broad, non-area-specific keyword "
            "pre-filter to merely LOOK property-related SOMEWHERE in its text (a BHK/RK "
            "mention, an area/price/location word, etc — that pre-filter says nothing "
            "about whether it's a real listing or which locality it's in). Your job has "
            "four parts: (1) decide whether each message is actually a property listing, "
            "(2) if it is, extract structured details from it, (3) for each extracted "
            "property, decide whether it falls inside one of the client's selected areas, "
            "and (4) for each extracted property, say whether its own text gave you enough "
            "to work with at all.",
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
            "STEP 0 — INVENTORY FIRST (do this before extracting anything from a message). "
            "Each message below is given to you with its ORIGINAL line breaks preserved, "
            "inside a <<<MESSAGE ... >>> block. Walk that text from the first line to the "
            "last and note, in \"property_lines\", every single line/bullet that advertises "
            "a property — one SHORT entry per property (a few words that identify it, like "
            "\"Olive Club Vesu 650sqft\"; do NOT copy the whole line out, that only makes "
            "the reply slower), in the order they appear. This is a counting/identifying "
            "step: you are only noting what is there, not yet judging, filtering, pricing, "
            "or area-matching anything. Then "
            "extract one \"properties\" entry per snippet you just listed. The two lists "
            "MUST end up the same length and in the same order — if you wrote 8 snippets, "
            "you return 8 properties. Counting the properties up front, in writing, before "
            "extracting them is what stops a long bullet list from quietly losing its last "
            "few entries; do not skip it, and never shorten \"properties\" below "
            "\"property_lines\" for any reason whatsoever.",
            "",
            "STEP 0 is ONLY about \"property_lines\" — it is the one place raw text is copied "
            "as-is. It does not change how the \"properties\" entries themselves are filled: "
            "each one is still fully extracted per the rules below, and area_name in "
            "particular must still be resolved to the LOCALITY (per PART 3, point 4) rather "
            "than left null just because the line's text was already transcribed into "
            "\"property_lines\". Never let the verbatim inventory become the extraction.",
            "",
            "NEVER DROP A LINE — once a line is in \"property_lines\", it gets its own "
            "\"properties\" entry, no matter what. In particular, none of these is EVER a "
            "reason to omit a property (they are the exact reasons real listings have been "
            "lost before):",
            "  - It looks incomplete or its numbers look odd/implausible (e.g. a rent shown "
            "as \"₹200/-\" that is obviously a per-sq-ft rate or a typo). Extract it as "
            "written, with whatever fields are actually stated and null for the rest — a "
            "strange-looking number is transcribed, never used to discard the property.",
            "  - It looks like a near-duplicate of another line in the same message (e.g. "
            "three separate \"VIP Road\" shops at different sizes/prices). Different size or "
            "different price means DIFFERENT properties — the same road appearing on several "
            "lines is normal for a broker with several units there. Never de-duplicate.",
            "  - Its locality is unfamiliar to you, or looks like it is outside the client's "
            "selected areas. That is decided in PART 3 via in_service_area, never by leaving "
            "the property out.",
            "  - It is far down a long list, or the list is longer than you expected. Length "
            "is never a reason to stop early — finish every line to the end of the message.",
            "",
            "WHAT COUNTS AS A PROPERTY — the one and only limit on the rule above. A line or "
            "fragment becomes its own entry only if it carries at least one concrete detail OF "
            "ITS OWN: a size, a price, a BHK/configuration, or a property type. A fragment that "
            "names ONLY a locality, landmark, road, or tick/emoji — with no size, no price, no "
            "BHK and no type of its own — is NOT a separate property. It is trailing text "
            "belonging to the property immediately before it: brokers routinely re-state the "
            "locality or leave a stray tick at the end of a line (e.g. \"... 650 Sq. Ft. - "
            "₹60,000/- ✅ Vesu\" is ONE shop in Vesu, not a shop plus a second property "
            "called \"Vesu\"). Fold such a fragment into the property it follows, and never emit "
            "it as its own entry or as a mostly-null property. Judge this per fragment: it never "
            "licenses dropping a line that DOES carry its own size/price/BHK/type, and two lines "
            "that each carry their own size or price are always two properties even if they share "
            "a road.",
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
            *step_a,
            "",
            "STEP B — per property, using the area knowledge from STEP A, decide "
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
            "no detail is lost — do not overwrite address with it, and do not remove it. "
            "NEVER leave area_name null on a property you marked in_service_area TRUE via "
            "point 1 or point 2: whichever selected area your area_match_reason cites is "
            "exactly what belongs in area_name. A property whose address you resolved to "
            "Vesu but whose area_name you left null is a FAILED extraction — the locality "
            "is what the client's own area matching runs on downstream, and an address "
            "string alone cannot stand in for it. Fill it for EVERY property, including "
            "every line of a long bulk listing.",
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
            "=== PART 4 — INFORMATION SUFFICIENCY (has_enough_information) ===",
            "",
            "For each extracted property, say whether its own text gave you enough to work with. "
            "A property you mark FALSE is pulled out of the normal list into a small manual queue "
            "and is excluded from client matching entirely until a human types its details in by "
            "hand — so this is expensive, and the bar for it is DELIBERATELY EXTREME.",
            "",
            "Set has_enough_information TRUE for essentially everything. A property is fine — TRUE "
            "— even when a lot is missing. All of these are NORMAL, WANTED listings and must be "
            "TRUE:",
            "  - No price at all (\"2 BHK flat in Vesu, contact 98xxxxxxx\") — TRUE.",
            "  - No area/locality, only a price and a size — TRUE.",
            "  - No BHK, no property type, no society name, no contact — TRUE.",
            "  - Only two facts in total, e.g. \"Shop 650 sqft, 60k\" — TRUE.",
            "  - Only ONE fact, if that fact identifies the property in a usable way: a locality, a "
            "society name, a size, a price, a BHK, or a property type — TRUE.",
            "Missing fields are the normal condition of a WhatsApp listing, never a reason to flag "
            "one. Do not flag a property because you wish it had more detail, because its numbers "
            "look odd, because you are unsure about its area, or because it is short.",
            "",
            "Set has_enough_information FALSE ONLY in the extreme case: the fragment carries "
            "essentially NOTHING you could extract — every single one of property_type, bhk, "
            "society_name, area_name, address, carpet_area_sqft, price_text and "
            "price_per_unit_text came out null or meaningless, so the property you are returning "
            "is an empty shell. Concretely: you have a line that is real text but says nothing "
            "usable about the property (\"available, interested people contact\", \"good deal, "
            "message me\", \"1 more in same building\"), leaving you with no location, no size, no "
            "price, no configuration and no type. If you can fill even ONE of those fields with "
            "something real from this property's own text, has_enough_information is TRUE.",
            "",
            "Write information_check_reason BEFORE has_enough_information — the same reason-first "
            "discipline as listing_type_reason and area_match_reason. State what the property's own "
            "text actually gave you (\"area Vesu + 2 BHK + 45L\", or \"nothing but 'contact for "
            "details'\"), then let the verdict follow from it. Never write the verdict first.",
            "",
            "WHEN AND ONLY WHEN has_enough_information is FALSE, also fill source_excerpt: copy "
            "VERBATIM the part of the message that refers to THIS property and nothing else — the "
            "line/bullet it came from plus any immediately adjacent wording belonging to it. A "
            "message can list ten properties; the other nine are not this one, so never copy the "
            "whole message, and never copy a different property's line. This exact text is what a "
            "human reads to complete the property by hand. Leave source_excerpt null whenever "
            "has_enough_information is TRUE — it costs output tokens and is not used there.",
            "",
            "This judgment is per property, exactly like listing_type and in_service_area: in a "
            "multi-property message, one useless fragment never makes its neighbors FALSE, and a "
            "detailed neighbor never rescues a genuinely empty fragment.",
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
            "      \"property_lines\": [\"<short identifying words for each property, per STEP 0>\"],",
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
            "\"in_service_area\": true or false, "
            "\"information_check_reason\": string, \"has_enough_information\": true or false, "
            "\"source_excerpt\": string|null}",
            "      ]",
            "    }",
            "  ]",
            "}",
            "",
            "Field order matters, because each step must be decided before the step that uses "
            "it: write \"property_lines\" BEFORE \"properties\", and inside each property object "
            "write listing_type_reason BEFORE listing_type, area_match_reason BEFORE "
            "in_service_area, and information_check_reason BEFORE has_enough_information, exactly "
            "as shown above — never a verdict first and its reason rationalized afterward. "
            "\"source_excerpt\" is null unless has_enough_information is false (see PART 4).",
            "",
            "\"extractions\" must contain exactly one object per input message, in the same "
            "order they were given, with \"source_message_id\" matching each message's id "
            "exactly. \"property_lines\" and \"properties\" always have the SAME number of "
            "entries in the same order; both are empty lists whenever is_property_listing is "
            "false.",
        ]
    )


def _build_user_prompt(batch: List[WhatsAppChatMessage], correction: Optional[str] = None) -> str:
    """Renders the batch for the LLM. Each message keeps its ORIGINAL line
    breaks inside a delimited <<<MESSAGE ...>>> block rather than being
    flattened onto one line: a bulk broker listing is structured almost
    entirely by its line/bullet layout, and collapsing every newline into a
    space left "one bullet = one property" (PART 2, STEP 0) with no bullets
    to anchor on — an 8-bullet shop listing came back with only 5
    properties. The delimiters are what keeps the per-message boundary
    unambiguous now that a message can itself span many lines.

    correction is set only on the one corrective re-ask fired when the model
    returned fewer properties than lines it had itself enumerated (see
    _recover_missed_properties) — normal calls pass None and are unaffected."""
    tracked_areas = area_filter_service.get_area_keywords()
    tracked_areas_list = ", ".join(tracked_areas) if tracked_areas else "(none configured)"
    lines = [
        f"Client-selected areas: {tracked_areas_list}.",
        "",
    ]
    cached_knowledge = _get_cached_area_knowledge(tracked_areas)
    if cached_knowledge:
        lines += [
            "Area knowledge (STEP A, already recalled for these exact areas — use this and set "
            f"the \"area_knowledge\" field to null):\n{cached_knowledge}",
            "",
        ]
    lines += [
        "This list is NOT a signal for is_property_listing (PART 1) — judge that purely on "
        "whether it's a genuine listing/request, regardless of area. It IS what you match "
        "each property against for in_service_area (PART 3).",
        "",
        "Messages: each one is delimited below. Everything after its \"text:\" line, up to the "
        "<<<END MESSAGE>>> marker, is that single message's raw text with its original line "
        "breaks intact — those lines/bullets are what you inventory in PART 2, STEP 0.",
        "",
        "source_message_id must be EXACTLY the value after \"id=\" on the <<<MESSAGE ...>>> "
        "line and nothing else — just that one token. Never append the group name, the "
        "\"group:\" line, the angle brackets, or any other part of the header to it, and never "
        "invent or reformat an id. An id that does not match character-for-character cannot be "
        "matched back to its message.",
        "",
    ]
    for message in batch:
        # The id sits ALONE on the marker line, with the group name moved to
        # its own labelled line below. When both shared that line, GLM was
        # observed copying the whole header into source_message_id (e.g.
        # 'ABC123 group="AI"'), which then matched no message and silently
        # discarded the entire batch — see _resolve_message_id, which cleans
        # up after that failure mode if it ever recurs.
        lines.append(f"<<<MESSAGE id={message.message_id}>>>")
        lines.append(f"group: {message.chat_name}")
        lines.append("text:")
        lines.append(message.text.strip())
        lines.append("<<<END MESSAGE>>>")
        lines.append("")
    if correction:
        lines.append(correction)
    return "\n".join(lines)


def _parse_extractions(content: str, batch_size: int) -> List[GLMPropertyExtraction]:
    """`content` is the model's raw reply text, already assembled from the
    streamed chunks by _stream_completion (which guarantees it is non-empty)."""
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
        # Keep it for the next batch so STEP A is paid for once per area list
        # rather than once per request (see _area_knowledge_cache).
        _remember_area_knowledge(area_filter_service.get_area_keywords(), parsed.area_knowledge)

    return parsed.extractions


def _resolve_message_id(returned_id: Optional[str], messages_by_id: dict) -> Optional[str]:
    """Maps the source_message_id GLM echoed back onto a real message in the
    batch, tolerating the model decorating it with surrounding prompt text.

    An exact match is the normal case and is taken immediately. The fallback
    exists because GLM has been observed copying the whole prompt header
    into this field (e.g. 'ABC123 group="AI"' instead of 'ABC123'): every
    extraction for that message then failed to match, and the batch's
    properties were discarded in full — the single worst outcome this
    pipeline has, caused by a cosmetic transcription slip. So when exactly
    ONE batch id appears inside the returned string, that is unambiguously
    the message being referred to and it is used. Ambiguity is never
    guessed at: if two ids somehow both appear, None is returned and the
    caller discards the extraction as before."""
    if not returned_id:
        return None
    if returned_id in messages_by_id:
        return returned_id
    contained = [message_id for message_id in messages_by_id if message_id and message_id in returned_id]
    if len(contained) != 1:
        return None
    step_logger.warn(
        f"GLM returned source_message_id {returned_id!r}, which is message {contained[0]!r} with "
        "extra prompt text attached — matching it back to that message rather than discarding "
        "real properties over a transcription slip."
    )
    return contained[0]


def _merge_with_message_data(
    extractions: List[GLMPropertyExtraction], batch: List[WhatsAppChatMessage]
) -> List[StructuredProperty]:
    messages_by_id = {message.message_id: message for message in batch}
    seen_ids = set()
    properties: List[StructuredProperty] = []

    for extraction in extractions:
        resolved_id = _resolve_message_id(extraction.source_message_id, messages_by_id)
        if resolved_id is None:
            step_logger.warn(
                "GLM returned an extraction for an unknown message id "
                f"({extraction.source_message_id!r}); discarding it."
            )
            continue
        message = messages_by_id[resolved_id]
        seen_ids.add(resolved_id)

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
    StructuredProperty records here — each embedded on its own downstream
    (see property_pipeline_service.handle_batch_ready) and each judged on
    its own by every rule below, exactly as if they had arrived in separate
    messages.

    A listing the LLM decided is outside every client-selected area
    (PART 3 of the prompt — in_service_area) is never dropped here: it is
    still stored, just flagged review_status="outsider" with the reason so
    it surfaces in the Outsider tab instead of silently vanishing — losing
    a wanted property is the one thing this pipeline must never do, and a
    wrongly-flagged outsider is still fully visible and correctable. The
    same goes for a listing carrying almost no information (PART 4): stored
    and flagged needs_review, never discarded — see
    _apply_information_review.

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
    _fill_missing_area_name(structured, listing.area_match_reason)
    _rescue_wrongly_flagged_outsider(structured)
    _sanitize_and_parse_prices(structured)
    _fill_missing_price_or_area(structured)
    if single_property_message:
        _sanitize_listing_type(structured)
    # Last, deliberately: the derivations above can fill in a price or an
    # area the LLM left null, and a property is judged on what actually
    # ended up stored, not on the raw extraction.
    _apply_information_review(structured, listing)
    return structured


# The three kinds of fact that make a stored property usable at all. Grouped
# rather than counted field-by-field because they are not interchangeable:
# "2 BHK Flat" with no location and no price is one kind of fact repeated,
# not three. A property that can answer two of these three questions —
# where is it, what is it, what does it cost — is a perfectly ordinary
# listing.
_INFORMATION_GROUPS = (
    ("location", ("society_name", "area_name", "address")),
    ("specification", ("property_type", "bhk", "carpet_area_sqft")),
    ("price", ("price_text", "price_amount_inr", "price_per_unit_text", "price_per_unit_amount_inr")),
)


def _filled_information_groups(prop: StructuredProperty) -> List[str]:
    return [
        name
        for name, fields in _INFORMATION_GROUPS
        if any(getattr(prop, field) not in (None, "", []) for field in fields)
    ]


def _apply_information_review(prop: StructuredProperty, listing: GLMPropertyListing) -> None:
    """Decides needs_review — "this property carries almost nothing" — from
    the LLM's PART 4 verdict, but never on that verdict alone.

    Same "never let the LLM be the only line of defense" pattern as
    _sanitize_listing_type and _verify_total_price_against_text, applied in
    both directions, because both mistakes are real:

      - The LLM flags a property that is actually fine. This queue is only
        useful if it stays tiny — a human hand-completing properties does
        not scale, and every flagged property is withheld from client
        matching until they get to it. So a flag is honoured ONLY when what
        was actually extracted agrees: at most one of the three
        _INFORMATION_GROUPS (location / specification / price) has anything
        in it. A property with two or more is unflagged here no matter what
        the model said, since by definition it can be matched and shown.
      - The LLM passes a property that is genuinely an empty shell (all
        three groups empty). Nothing about it can be displayed, matched or
        searched, so it goes to the queue regardless — it would otherwise
        sit in the Main list as a blank row forever.

    For a property that ends up flagged, `description` is replaced with the
    LLM's verbatim excerpt of just this property's own part of the message.
    The full message text is untouched and still shown in the dialog as
    always — but a ten-property message is useless as the thing a human
    reads to complete ONE of them, which is exactly what the excerpt fixes.
    """
    filled = _filled_information_groups(prop)
    model_flagged = listing.has_enough_information is False
    flagged = len(filled) == 0 or (model_flagged and len(filled) <= 1)

    if not flagged:
        # Nothing to undo — needs_review defaults to False and review_notes
        # carries only the outsider reason, if any.
        if model_flagged:
            step_logger.info(
                f"GLM flagged the property from message {prop.source_message_id!r} as too thin to use, but "
                f"it has usable {'/'.join(filled)} data — keeping it in the normal list rather than "
                "sending a matchable property to the manual queue."
            )
        return

    # The model's own words whenever the model is the one that flagged it —
    # a human working the queue wants to know what it did and didn't find.
    # When the flag is ours alone (an empty shell the model passed), its
    # reason argues the opposite case, so the explanation has to be ours.
    reason = (listing.information_check_reason or "").strip() if model_flagged else ""
    if not reason:
        reason = "Nothing usable could be extracted from this property's part of the message."

    prop.needs_review = True
    # review_notes is one free-text field shared with the outsider reason
    # (set above by _to_structured_property), so append rather than
    # overwrite — losing why a property was marked outsider would be a real
    # regression, not a cosmetic one.
    prop.review_notes = f"{prop.review_notes} | {reason}" if prop.review_notes else reason
    excerpt = (listing.source_excerpt or "").strip()
    if excerpt:
        prop.description = excerpt
    step_logger.warn(
        f"Property from message {prop.source_message_id!r} has almost no usable information "
        f"(groups filled: {'/'.join(filled) or 'none'}) — queued for manual completion: {reason}"
    )


def _first_selected_area_in(text: Optional[str], selected_areas: List[str]) -> Optional[str]:
    """The client-selected area named in `text`, or None. Longest match wins,
    so a text mentioning "Udhna Magdalla" resolves to "Udhna Magdalla" rather
    than "Udhna" when the client has selected both."""
    if not text:
        return None
    for area in sorted(selected_areas, key=len, reverse=True):
        if re.search(rf"\b{re.escape(area)}\b", text, re.IGNORECASE):
            return area
    return None


def _fill_missing_area_name(prop: StructuredProperty, area_match_reason: Optional[str]) -> None:
    """Deterministic safety net for a NULL area_name — the same "never let the
    LLM be the only line of defense" pattern as _sanitize_listing_type and
    _verify_total_price_against_text.

    PART 3 point 4 tells the model to resolve area_name to the client-selected
    LOCALITY (e.g. address "VIP Road" -> area_name "Vesu"), but on long bulk
    listings GLM-4.7-FlashX routinely fills only address and leaves area_name
    null on every line after the first. area_name is what the client's own
    area matching runs on downstream, so a null there quietly costs matches
    even though the property was stored.

    Two grounded sources, tried in order, and ONLY ever used to fill a null —
    an area_name the LLM did set is never touched:

    1. The property's OWN address/society text, when it literally names a
       selected area ("Anurodh Dwar, Citylight Char Rasta" -> "Citylight").
       Preferred because it is the property's own words, not a judgement.
    2. Failing that, the selected area the model itself cited in
       area_match_reason ("VIP Road is a well-known road in Vesu" -> "Vesu")
       — its PART 3 verdict, just written into the reason field instead of
       area_name. Only consulted for a property it accepted as in-service-
       area: on an outsider, area_match_reason names a selected area
       precisely to say the property is NOT in it.
    """
    if prop.area_name:
        return
    selected_areas = [area.strip() for area in area_filter_service.get_area_keywords() if area.strip()]
    if not selected_areas:
        return

    resolved = _first_selected_area_in(prop.address, selected_areas) or _first_selected_area_in(
        prop.society_name, selected_areas
    )
    source = "its own address"
    if resolved is None and prop.review_status == "accepted":
        resolved = _first_selected_area_in(area_match_reason, selected_areas)
        source = "the area GLM cited in its own area_match_reason"
    if resolved is None:
        return

    step_logger.info(
        f"Property from message {prop.source_message_id!r} came back with no area_name "
        f"(address={prop.address!r}) — filled it as {resolved!r} from {source}, so it still "
        "matches against client requirements."
    )
    prop.area_name = resolved


def _rescue_wrongly_flagged_outsider(prop: StructuredProperty) -> None:
    """Un-flags an "outsider" whose own address literally names a client-
    selected area.

    PART 3 asks the model to match a property against the areas it recalled
    in STEP A, and it sometimes answers on that recall alone: "Udhna
    Magdalla" was marked OUTSIDER because the recalled line for Udhna
    happened to list "Udhna GIDC" and "Udhna Railway Station" but not
    Magdalla — even though the address opens with the selected area's own
    name. An outsider is hidden from the main list, so this quietly costs
    exactly the kind of listing the client asked to see.

    No judgement is involved here: if this property's OWN address, society
    or area text contains a selected area as a whole word, it is in that
    area, whatever the recall did or didn't mention. Strictly one-way — it
    only ever moves a property INTO the accepted list, never out of one, so
    it cannot suppress anything a human would otherwise review."""
    if prop.review_status != "outsider":
        return
    selected_areas = [area.strip() for area in area_filter_service.get_area_keywords() if area.strip()]
    if not selected_areas:
        return
    named = (
        _first_selected_area_in(prop.area_name, selected_areas)
        or _first_selected_area_in(prop.address, selected_areas)
        or _first_selected_area_in(prop.society_name, selected_areas)
    )
    if named is None:
        return
    step_logger.info(
        f"Property from message {prop.source_message_id!r} was flagged OUTSIDER, but its own "
        f"address/area ({prop.address or prop.area_name!r}) names the selected area {named!r} "
        "outright — accepting it instead of hiding a property the client asked for."
    )
    prop.review_status = "accepted"
    prop.review_notes = None
    if not prop.area_name:
        prop.area_name = named


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



# Ground-truth cross-check for the per-unit rate, matched directly against
# the broker's own raw wording (a number, an optional explicit scale word,
# then a "per <unit>"/"/ <unit>" phrase) rather than anything the LLM itself
# computed. Per-unit rates are exactly where a small/fast model's scale
# judgment (lakh vs thousand vs bare rupees) is least reliable: a plain
# "₹6,500 Per Sq. Ft." carries no scale word at all, and GLM has been
# observed inventing one anyway (treating it as "6.5L per sq ft" — a clean
# 100x error that then propagates into a wildly wrong total once
# _fill_missing_price_or_area multiplies it by the area). Unlike totals,
# which brokers almost always spell out with an explicit Lakh/Crore word,
# per-unit rates are routinely written as bare numbers, so this pattern is
# common enough to be worth guarding deterministically rather than trusting
# the LLM's transcription.
_UNIT_PHRASE_PATTERNS = {
    "sqft": r"sq\.?\s*ft\.?|sqft|square\s*feet",
    "vaar": r"vaar|gaj|sq\.?\s*yard|square\s*yard",
    "vigha": r"vigha",
}
_NUMBER_SCALE_PATTERN = (
    r"(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<scale>cr|crore|crores|lac|lacs|lakh|lakhs|thousand|l|k)?\b"
)


def _extract_per_unit_rate_from_text(text: Optional[str], unit: Optional[str]) -> Optional[float]:
    """Deterministically re-derives the per-unit rate straight from the raw
    message text for whichever unit (sqft/vaar/vigha) this property actually
    used, independent of the LLM's own price_per_unit_text/
    price_per_unit_amount_inr. Returns None when the unit is missing/
    unrecognized or no "<number><scale?> per/ <unit>" pattern is found in the
    text — callers must treat that as "no cross-check available", not as
    evidence the LLM's figure is wrong."""
    if not text or unit not in _UNIT_PHRASE_PATTERNS:
        return None
    pattern = re.compile(
        _NUMBER_SCALE_PATTERN + r"\s*(?:per\s+|/\s*)(?:" + _UNIT_PHRASE_PATTERNS[unit] + r")",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    if not match:
        return None
    value = float(match.group("num").replace(",", ""))
    scale = (match.group("scale") or "").lower()
    return value * _SCALE_MULTIPLIERS[scale] if scale else value


# Ground-truth cross-check for a SCALED total price (price_text ending in
# cr/L/lakh/k/...) — the same class of bug as the per-unit rate above, but
# on the total instead. A total the LLM writes as bare digits with no scale
# word ("8500000") carries no scale-confusion risk and is never checked
# here; the danger is specifically the LLM attaching a lakh/crore/k
# multiplier that has no basis in the message at all — observed producing a
# price_text/price_amount_inr pair for a message that never states a total
# price to begin with (only a per-unit rate), where the "total" is pure
# invention despite the prompt explicitly forbidding that. Scale is
# mandatory in this pattern (unlike _NUMBER_SCALE_PATTERN above) because a
# bare-digit total is exactly the safe case this check must leave alone.
_SCALED_TOTAL_PATTERN = (
    r"(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<scale>cr|crore|crores|lac|lacs|lakh|lakhs|thousand|l|k)\b"
)
_SCALED_PRICE_TEXT_RE = re.compile(
    r"(?:cr|crore|crores|lac|lacs|lakh|lakhs|thousand|l|k)\s*$", re.IGNORECASE
)


def _find_total_price_candidates(text: Optional[str]) -> List[float]:
    """Every standalone <number><scale word> mention in the raw message
    text that is NOT part of a per-unit ('per sqft'/'/vaar'/...) phrase —
    the set of plausible TOTAL price amounts actually written in the
    message. Empty when the message never states a scaled total at all
    (e.g. it only ever quotes a per-unit rate), which is exactly the signal
    _verify_total_price_against_text uses to catch a hallucinated total."""
    if not text:
        return []
    pattern = re.compile(
        _SCALED_TOTAL_PATTERN + r"(?!\s*(?:per\s+|/\s*)(?:" + "|".join(_UNIT_PHRASE_PATTERNS.values()) + r"))",
        re.IGNORECASE,
    )
    return [
        float(match.group("num").replace(",", "")) * _SCALE_MULTIPLIERS[match.group("scale").lower()]
        for match in pattern.finditer(text)
    ]


# Every plain number written in a message, Indian comma-grouping included
# ("60,000", "1,60,000", "650"). Used only to confirm that a total price the
# LLM reported is actually stated somewhere in the text.
_PLAIN_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _text_states_amount(text: Optional[str], amount: float) -> bool:
    """True if `amount` is written verbatim as a number in `text` — the
    broker's own figure, before any scale-word interpretation."""
    for match in _PLAIN_NUMBER_RE.finditer(text or ""):
        try:
            value = float(match.group(0).replace(",", ""))
        except ValueError:
            continue
        if math.isclose(value, amount, rel_tol=0.001):
            return True
    return False


def _verify_total_price_against_text(prop: StructuredProperty) -> None:
    """Cross-checks a SCALED total price against the raw message text (see
    _find_total_price_candidates). A total with no scale suffix is left
    untouched — there's no multiplier for the LLM to have gotten wrong.

    - If the message does state a standalone total and the LLM's figure
      disagrees, the text's own value wins (same rationale as the per-unit
      check: it's what the broker actually typed).
    - If the LLM's total has NO basis anywhere in the text — the message
      never states a total at all, only e.g. a per-unit rate — the LLM
      invented it despite being told never to guess. Clear price_text/
      price_amount_inr so _fill_missing_price_or_area derives the real
      total deterministically from area x per-unit rate instead of a
      fabricated number surviving into the stored record.
    """
    if prop.price_amount_inr is None or not _SCALED_PRICE_TEXT_RE.search(prop.price_text or ""):
        return

    # Grounded already? A broker who writes a total in full ("₹60,000/-")
    # states no scale word, so _find_total_price_candidates — which requires
    # one — sees no total at all and this function would wrongly conclude the
    # LLM invented it, wiping a perfectly good price off the record. (Seen on
    # exactly that message: price_text "60k", text "₹60,000/-".) Matching the
    # amount against the plain numbers actually written settles it. Only ever
    # used to SKIP the correction below, never to rewrite a price, so a
    # coincidental number in the text can't change any value.
    if _text_states_amount(prop.message_text, prop.price_amount_inr):
        return

    candidates = _find_total_price_candidates(prop.message_text)
    if any(math.isclose(prop.price_amount_inr, candidate, rel_tol=0.01) for candidate in candidates):
        return

    if candidates:
        best = min(candidates, key=lambda candidate: abs(candidate - prop.price_amount_inr))
        step_logger.warn(
            f"Total price for message {prop.source_message_id!r} disagreed with the raw message text "
            f"(LLM gave {prop.price_amount_inr:,.0f}, text says {best:,.0f}) — using the text-grounded value."
        )
        prop.price_amount_inr = best
        prop.price_text = _format_compact_inr(best)
    else:
        step_logger.warn(
            f"Total price for message {prop.source_message_id!r} ({prop.price_text!r} / "
            f"{prop.price_amount_inr:,.0f} INR) has no basis anywhere in the raw message text — the LLM "
            "invented a total that was never actually stated. Clearing it so it derives deterministically "
            "from area x per-unit rate instead."
        )
        prop.price_amount_inr = None
        prop.price_text = None


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
    3. Cross-check the resulting per-unit rate against a fresh parse of the
       raw message text (_extract_per_unit_rate_from_text). When the two
       disagree, the raw text wins — it's what the broker actually typed,
       while the LLM's number/scale is a transcription that can silently
       apply the wrong magnitude (see _extract_per_unit_rate_from_text's
       docstring). This also repairs price_per_unit_text so it stays
       consistent with the corrected amount.
    4. Same idea for a SCALED total price (_verify_total_price_against_text)
       — cross-check it against the raw text, and clear it entirely if it
       has no basis there at all (the LLM invented a total for a message
       that only ever stated a per-unit rate), letting the deterministic
       area x rate derivation below fill in the real one instead.
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

    _verify_total_price_against_text(prop)

    grounded_rate = _extract_per_unit_rate_from_text(prop.message_text, prop.carpet_area_unit)
    if (
        grounded_rate is not None
        and grounded_rate > 0
        and prop.price_per_unit_amount_inr is not None
        and not math.isclose(grounded_rate, prop.price_per_unit_amount_inr, rel_tol=0.01)
    ):
        step_logger.warn(
            f"Per-unit rate for message {prop.source_message_id!r} disagreed with the raw message text "
            f"(LLM gave {prop.price_per_unit_amount_inr:,.0f}/{prop.carpet_area_unit}, text says "
            f"{grounded_rate:,.0f}/{prop.carpet_area_unit}) — using the text-grounded value. This is almost "
            "always the LLM mis-scaling a bare number (e.g. reading '6,500 per sq ft' as '6.5L per sq ft')."
        )
        prop.price_per_unit_amount_inr = grounded_rate
        prop.price_per_unit_text = f"{_format_compact_inr(grounded_rate)}/{prop.carpet_area_unit}"


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
        return f"{_trim_number(amount / _THOUSAND)}K"
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
