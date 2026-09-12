"""LLM-driven structuring stage for broker REQUIREMENTS — the demand-side
counterpart of property_structurer.py.

It turns a batch of up to 10 free-form WhatsApp messages already judged to
be requirements into StructuredRequirement records, in a single prompt per
batch, using the same GLM model and the same streamed transport/retry policy
as the property stage (see glm_client.py).

Two things feed that batch. Most messages come from the cheap string filter
(Service/WhatsAppDataFetchingService/requirement_filter_service.py), which
matches a demand by its wording. The rest are messages the PROPERTY stage
read and re-routed here because they turned out to be demands phrased
without any of those trigger words ("I want to look for 3bhk flat in
vesu") — those arrive carrying `reclassified_as_requirement`, and the
prompt is told so, because they have already been judged once and should
not be bounced back out into nowhere.

Deliberately much smaller than the property stage, because the product
decision behind this feature is that a requirement needs far less machinery
than a listing does:

  - NO area matching. A property is judged against the client's selected
    areas and flagged Main/Outsider; a requirement is not judged at all —
    every area it names is copied as written. That removes the whole STEP A
    area-recall section (the single most expensive part of the property
    prompt) and its cache.
  - NO duplicate detection and NO embeddings. Two brokers asking for the
    same thing are two real requirements, not a duplicate to resolve.
  - NO review queue. There is no Main/Outsider/Needs-review concept here.

What IS kept, because both were hard-won on the property side:
  - One message can carry MANY requirements, and each becomes its own
    record. The "enumerate the lines first, then extract one entry per
    line" discipline (requirement_lines) is what makes that reliable.
  - Rent vs Sale is decided reason-first, verdict-second, and defaults to
    "Sale" when the message carries no explicit rental signal — plus a
    deterministic safety net below for the single-requirement case.

Never raises: a batch that still fails after retries is logged and skipped
rather than crashing the caller.
"""

from __future__ import annotations

import json
import re
from typing import List, Optional

from pydantic import ValidationError

from Agent.WhatsAppDataFetchingAgent import glm_client
from Agent.WhatsAppDataFetchingAgent.glm_requirement_schema import (
    GLMRequirementExtraction,
    GLMRequirementItem,
    GLMRequirementResponse,
)
from Agent.WhatsAppDataFetchingAgent.price_scales import (
    SCALE_MULTIPLIERS as _SCALE_MULTIPLIERS,
    SCALE_WORD_PATTERN as _SCALE_WORD_PATTERN,
)
from Middleware import step_logger
from Model.WhatsAppDataFetchingModel.broker_requirement import StructuredRequirement
from Model.WhatsAppDataFetchingModel.whatsapp_message import WhatsAppChatMessage

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

_BUDGET_CLEAN_RE = re.compile(r"[₹,]|rs\.?|inr", re.IGNORECASE)
# "50L and above" / "under 50L" — one figure, but only one END. Used solely
# by the numeric fallback below, never to override the model's own numbers.
_OPEN_UPPER_RE = re.compile(r"\+|above|onwards?|plus|minimum|min\b|upar|thi\s*upar|se\s*upar", re.IGNORECASE)
_OPEN_LOWER_RE = re.compile(r"under|below|up\s*to|upto|within|maximum|max\b|sudhi", re.IGNORECASE)
_BUDGET_NUMBER_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(" + _SCALE_WORD_PATTERN + r")?", re.IGNORECASE
)

# Same deterministic Rent/Sale safety net the property stage uses, and for
# the same reason: never trust the model's classification as the ONLY line
# of defence. Only ever additive toward "Rent" — an LLM call of "Rent" is
# never overridden back to "Sale", since a stray "sale" substring is far
# weaker evidence than an explicit rent word is.
_RENT_KEYWORDS = (
    "rent", "on rent", "for rent", "rent par", "rent pe", "rental", "to let", "lease",
    "bhade", "bhada", "bhadu", "bhado", "bhadey", "bahde", "bahda", "bahdu", "kiraya", "kiraye",
)
_SALE_KEYWORDS = (
    "sale", "for sale", "to buy", "buy", "purchase", "resale",
    "kharid", "kharidna", "kharidvu", "kharidvanu", "levu", "levanu", "lena",
)
_RENT_SIGNAL_RE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in _RENT_KEYWORDS) + r")\b", re.IGNORECASE)
_SALE_SIGNAL_RE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in _SALE_KEYWORDS) + r")\b", re.IGNORECASE)


def structure_batch(batch: List[WhatsAppChatMessage]) -> List[StructuredRequirement]:
    """Sends one batch (up to 10 messages) to GLM in a single prompt and
    returns StructuredRequirement records for the messages that turned out
    to be actual requirements. Never raises: a batch that still fails after
    retries is logged and skipped."""
    if not batch:
        return []

    request_body = glm_client.build_request_body(_build_system_prompt(), _build_user_prompt(batch))
    content = glm_client.post_with_retries(request_body, f"a requirement batch of {len(batch)}")
    if content is None:
        return []

    extractions = _parse_extractions(content, len(batch))
    return _merge_with_message_data(extractions, batch)


def _build_system_prompt() -> str:
    return "\n".join(
        [
            "You extract structured REQUIREMENTS (property DEMANDS) from raw WhatsApp messages sent in Indian "
            "real-estate broker groups, mostly around Surat, Gujarat. Messages are written informally in "
            "English, Hindi, Gujarati, or a mix, often transliterated into the Latin alphabet.",
            "",
            "A REQUIREMENT is someone LOOKING FOR a property — to buy or to rent. It is the opposite of a "
            "listing: a listing OFFERS a property, a requirement ASKS for one. Typical requirement wording "
            'includes "requirement", "required", "req", "wanted", "looking for", "chahiye", "zaroorat hai", '
            '"joie chhe", "jarurat", "need".',
            "",
            "You are given a batch of messages. Return ONE object per message, in the same order, each keyed by "
            "that message's exact id.",
            "",
            "=====================================================================",
            "PART 1 — IS THIS A REQUIREMENT?",
            "=====================================================================",
            "Set is_requirement to true ONLY when the message is genuinely asking for / looking for a property.",
            "",
            "Set it to FALSE for:",
            "  - a property being OFFERED for sale or rent (that is a listing, not a requirement), even if the "
            'word "required" appears somewhere in it (e.g. "brokerage required", "documents required", '
            '"advance required" — those are conditions attached to an OFFER, not a demand for a property);',
            "  - greetings, festival wishes, forwards, jokes, general chit-chat;",
            "  - questions about paperwork, loans, rates or the market that do not ask for a specific property;",
            '  - someone asking for a BROKER, a partner, a buyer or a tenant rather than a property.',
            "When is_requirement is false, write a short skip_reason and return an EMPTY requirements list.",
            "",
            "=====================================================================",
            "PART 2 — COUNT THE REQUIREMENTS BEFORE EXTRACTING ANY OF THEM",
            "=====================================================================",
            "One message very often carries SEVERAL separate requirements — a broker forwarding three clients' "
            "needs in one text, one per line or bullet.",
            "",
            "STEP 0 (do this FIRST, before extracting any field): walk the message from top to bottom and write "
            'a SHORT snippet for every distinct requirement you meet into "requirement_lines". Filter nothing, '
            "merge nothing, skip nothing at this step — just inventory them. A handful of words per entry is "
            'enough to tell them apart (e.g. "3BHK Vesu 80L", "Shop VIP Road rent").',
            "",
            'STEP 1: "requirements" must then contain EXACTLY ONE entry per snippet in "requirement_lines", in '
            "the same order, with the same count. No exceptions.",
            "",
            "Two requirements are DISTINCT when they differ in what is being asked for — a different BHK, a "
            "different property type, a different area, a different budget, or a different client. Do NOT split "
            "one requirement's own details (its area list, its amenities, its floor preference) into several "
            "entries.",
            "",
            "=====================================================================",
            "PART 3 — AREAS ARE COPIED, NEVER JUDGED",
            "=====================================================================",
            "This is the most important difference from listing extraction: you are NOT given a list of areas "
            "to match against, and you must NOT decide whether a requirement's area is wanted or unwanted, "
            "in-service or out-of-service. There is no such judgement here.",
            "",
            "Copy every locality/area the requirement names into preferred_areas EXACTLY as written in the "
            "message. Do not normalise the spelling, do not translate it, do not correct it, do not expand an "
            "abbreviation, and never add an area the message did not name. If the requirement names no area at "
            "all, return an empty list — do not infer one.",
            "",
            "=====================================================================",
            "RENT VS SALE CLASSIFICATION",
            "=====================================================================",
            "For EACH requirement, decide whether the person wants to BUY (\"Sale\") or to RENT (\"Rent\").",
            "",
            "Write listing_type_reason FIRST, quoting the actual wording in THIS message for THIS requirement, "
            "and only then set listing_type from it.",
            "",
            'Explicit RENT signals include: "rent", "on rent", "rent pe", "rental", "lease", "to let", '
            '"bhade", "bhada", "kiraya", "monthly", a monthly amount like "15k/month".',
            'Explicit BUY signals include: "buy", "purchase", "sale", "kharidvu", "kharidna", "levu", "lena".',
            "",
            "If there is NO explicit rental signal for this requirement, set listing_type to \"Sale\" and write "
            '"no explicit signal" as the reason. Never invent a signal that is not in the message.',
            "",
            "In a message carrying several requirements, judge each one on ITS OWN line — a rent word on one "
            "line says nothing about a different line.",
            "",
            "=====================================================================",
            "BUDGET AND SIZE",
            "=====================================================================",
            "A requirement has a BUDGET (a range the person is willing to pay), not a price, and a SIZE RANGE, "
            "not an exact carpet area.",
            "",
            "  - A stated range fills both ends: \"40 to 50 lakh\" -> budget_min_inr 4000000, budget_max_inr "
            "5000000.",
            "  - A single figure fills BOTH ends with the same value: \"45 lakh\" -> 4500000 and 4500000.",
            "  - A one-sided limit fills only that side: \"under 50L\" -> max only; \"50L and above\" -> min only.",
            "  - The same three rules apply to carpet_area_min / carpet_area_max.",
            "  - NEVER guess a budget or a size that is not written, and never estimate one from the BHK.",
            "  - Copy the size number exactly as written for whichever unit is used, and never convert between "
            "units. Set carpet_area_unit whenever either size is set.",
            "",
            "=====================================================================",
            "GENERAL RULES",
            "=====================================================================",
            "  - Extract ONLY what the message actually says. Every field is optional; null is always better "
            "than a guess.",
            "  - contact_name / contact_phone come from the MESSAGE TEXT only — never from the sender's WhatsApp "
            "profile, which is already known and is merged in separately.",
            "  - Never copy the message id, the group name or any part of the prompt into a content field.",
            "",
            "=====================================================================",
            "OUTPUT FORMAT",
            "=====================================================================",
            "Return ONE JSON object, and nothing else — no prose, no markdown, no code fences:",
            "",
            "{",
            '  "extractions": [',
            "    {",
            '      "source_message_id": "<exactly the id given for this message>",',
            '      "is_requirement": true,',
            '      "requirement_lines": ["<short snippet per requirement>"],',
            '      "requirements": [',
            "        {",
            '          "requirement_type": null, "bhk": null, "preferred_areas": [], "society_name": null,',
            '          "address": null, "carpet_area_min": null, "carpet_area_max": null,',
            '          "carpet_area_unit": null, "budget_text": null, "budget_min_inr": null,',
            '          "budget_max_inr": null, "furnishing": null, "listing_type_reason": "...",',
            '          "listing_type": "Sale", "contact_name": null, "contact_phone": null, "description": null',
            "        }",
            "      ],",
            '      "skip_reason": null',
            "    }",
            "  ]",
            "}",
            "",
            "Include one extraction object for EVERY message in the batch, including the ones you judge not to "
            "be requirements (those simply have is_requirement false, empty lists, and a skip_reason).",
        ]
    )


def _build_user_prompt(batch: List[WhatsAppChatMessage]) -> str:
    """Renders the batch for the LLM. Each message keeps its ORIGINAL line
    breaks inside a delimited <<<MESSAGE ...>>> block: a bulk requirement
    post is structured almost entirely by its line/bullet layout, and
    flattening the newlines leaves "one bullet = one requirement" (PART 2,
    STEP 0) with no bullets to anchor on.

    The id sits ALONE on the marker line with the group name on its own
    labelled line below — on the property side, sharing that line led to the
    model copying the whole header into source_message_id, which matched no
    message and silently discarded the batch."""
    lines = [
        "Every message below has already been flagged as looking like a REQUIREMENT before reaching you. Most "
        "were flagged by a keyword filter, which is deliberately loose — so verify those yourself in PART 1: a "
        "message that is actually a property being offered must come back with is_requirement false, not be "
        "forced into a requirement.",
        "",
        "A message whose block carries a \"pre-classified: DEMAND\" line is different. That one has already "
        "been read in full by the listing-extraction stage, which concluded it is someone ASKING for a "
        "property rather than offering one, and re-routed it here on that basis. Treat it as a requirement "
        "and extract it: set is_requirement true and fill in whatever it states. Only return is_requirement "
        "false for such a message if its text plainly OFFERS a specific property (a property being advertised "
        "with its price and contact), which would mean the earlier stage misread it.",
        "",
        "Messages: each one is delimited below. Everything after its \"text:\" line, up to the <<<END MESSAGE>>> "
        "marker, is that single message's raw text with its original line breaks intact — those lines/bullets "
        "are what you inventory in PART 2, STEP 0.",
        "",
        "source_message_id must be EXACTLY the value after \"id=\" on the <<<MESSAGE ...>>> line and nothing "
        "else — just that one token. Never append the group name, the \"group:\" line, the angle brackets, or "
        "any other part of the header to it, and never invent or reformat an id. An id that does not match "
        "character-for-character cannot be matched back to its message.",
        "",
    ]
    for message in batch:
        lines.append(f"<<<MESSAGE id={message.message_id}>>>")
        lines.append(f"group: {message.chat_name}")
        if message.reclassified_as_requirement:
            lines.append("pre-classified: DEMAND (already read and judged by the listing-extraction stage)")
        lines.append("text:")
        lines.append(message.text.strip())
        lines.append("<<<END MESSAGE>>>")
        lines.append("")
    return "\n".join(lines)


def _parse_extractions(content: str, batch_size: int) -> List[GLMRequirementExtraction]:
    content = _CODE_FENCE_RE.sub("", content.strip()).strip()

    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        step_logger.error(f"GLM response was not valid JSON for a requirement batch of {batch_size}: {exc}")
        return []

    try:
        parsed = GLMRequirementResponse.model_validate(raw)
    except ValidationError as exc:
        step_logger.error(
            f"GLM response didn't match the expected requirement schema for a batch of {batch_size}: {exc}"
        )
        return []

    return parsed.extractions


def _resolve_message_id(returned_id: Optional[str], messages_by_id: dict) -> Optional[str]:
    """Maps the source_message_id GLM echoed back onto a real message in the
    batch, tolerating the model decorating it with surrounding prompt text —
    the exact same recovery the property stage needed after GLM was observed
    copying the whole prompt header into this field, which discarded a whole
    batch over a cosmetic transcription slip. Ambiguity is never guessed at:
    if two ids somehow both appear, None is returned."""
    if not returned_id:
        return None
    if returned_id in messages_by_id:
        return returned_id
    contained = [message_id for message_id in messages_by_id if message_id and message_id in returned_id]
    if len(contained) != 1:
        return None
    step_logger.warn(
        f"GLM returned source_message_id {returned_id!r}, which is message {contained[0]!r} with extra prompt "
        "text attached — matching it back to that message rather than discarding real requirements."
    )
    return contained[0]


def _merge_with_message_data(
    extractions: List[GLMRequirementExtraction], batch: List[WhatsAppChatMessage]
) -> List[StructuredRequirement]:
    messages_by_id = {message.message_id: message for message in batch}
    seen_ids = set()
    requirements: List[StructuredRequirement] = []

    for extraction in extractions:
        resolved_id = _resolve_message_id(extraction.source_message_id, messages_by_id)
        if resolved_id is None:
            step_logger.warn(
                "GLM returned a requirement extraction for an unknown message id "
                f"({extraction.source_message_id!r}); discarding it."
            )
            continue
        message = messages_by_id[resolved_id]
        seen_ids.add(resolved_id)

        if not extraction.is_requirement or not extraction.requirements:
            step_logger.info(
                f"Skipped (not a requirement): {extraction.skip_reason or 'no reason given'} — {message.text[:80]!r}"
            )
            continue

        if len(extraction.requirements) > 1:
            step_logger.info(
                f"Message {message.message_id!r} contains {len(extraction.requirements)} distinct requirements — "
                "structuring each separately."
            )

        for item in extraction.requirements:
            # Logged for every requirement, not just the Rent ones: this is
            # the only visibility into whether listing_type reflects genuine
            # per-requirement reasoning or a silently defaulted/omitted field
            # (GLMRequirementItem.listing_type fails open to "Sale").
            step_logger.info(
                f"Requirement from message {message.message_id!r} -> listing_type={item.listing_type!r}: "
                f"{item.listing_type_reason or 'no reason given by GLM'}"
            )
            requirements.append(
                _to_structured_requirement(
                    item, message, single_requirement_message=len(extraction.requirements) == 1
                )
            )

    for missing_id in set(messages_by_id) - seen_ids:
        step_logger.warn(
            f"GLM did not return anything for requirement message id {missing_id!r} — dropped from this batch."
        )

    return requirements


def _to_structured_requirement(
    item: GLMRequirementItem, message: WhatsAppChatMessage, single_requirement_message: bool
) -> StructuredRequirement:
    """Builds one StructuredRequirement from one extracted item, merged with
    the WhatsApp metadata shared by every requirement pulled from that same
    message. Two items from one message become two fully independent records
    here, exactly as if they had arrived in separate messages."""
    areas = [area.strip() for area in item.preferred_areas if area and area.strip()]
    requirement = StructuredRequirement(
        source_message_id=message.message_id,
        source_connection_id=message.connection_id,
        requirement_type=item.requirement_type,
        bhk=item.bhk,
        # The Area column shows one value; the full list lives alongside it
        # so nothing the message named is lost.
        area_name=areas[0] if areas else None,
        preferred_areas=areas,
        society_name=item.society_name,
        address=item.address,
        carpet_area_min=item.carpet_area_min,
        carpet_area_max=item.carpet_area_max,
        carpet_area_unit=item.carpet_area_unit,
        budget_text=item.budget_text,
        budget_min_inr=item.budget_min_inr,
        budget_max_inr=item.budget_max_inr,
        listing_type=item.listing_type,
        furnishing=item.furnishing,
        contact_name=item.contact_name,
        contact_phone=item.contact_phone,
        description=item.description,
        group_name=message.chat_name,
        chat_type=message.chat_type,
        sender_name=message.sender_name,
        sender_saved_name=message.sender_saved_name,
        sender_phone=message.sender_phone,
        message_text=message.text,
        message_timestamp=message.received_at,
    )
    _fill_missing_budget_amounts(requirement)
    _normalize_ranges(requirement)
    if single_requirement_message:
        _sanitize_listing_type(requirement)
    return requirement


def _fill_missing_budget_amounts(requirement: StructuredRequirement) -> None:
    """Deterministic fallback for when the model filled budget_text but left
    both numeric ends null — a plainly parseable "45L" or "40L-50L" should
    never end up unusable just because the model forgot the numeric fields.
    Only ever FILLS nulls; a value the model did provide is never
    overwritten here."""
    if requirement.budget_min_inr is not None or requirement.budget_max_inr is not None:
        return
    text = requirement.budget_text
    if not text:
        return
    amounts = _parse_budget_amounts(text)
    if not amounts:
        return
    if len(amounts) == 1:
        # A single figure normally means an exact budget (both ends), but
        # "50L and above" / "under 50L" are one figure with only ONE end.
        # Filling both from those would invent a bound the broker never
        # gave — a ceiling of 50L on a requirement that explicitly said
        # 50L+ is worse than leaving that end unknown.
        if _OPEN_UPPER_RE.search(text):
            requirement.budget_min_inr = amounts[0]
            return
        if _OPEN_LOWER_RE.search(text):
            requirement.budget_max_inr = amounts[0]
            return
    requirement.budget_min_inr = amounts[0]
    requirement.budget_max_inr = amounts[-1]


def _parse_budget_amounts(text: str) -> List[float]:
    """Every INR amount named in a budget string, in order — one for "45L",
    two for "40L-50L". Each number carries its own scale word when it has
    one; a bare number in a range inherits the scale of the last number that
    did have one ("40-50 lakh" -> 4000000, 5000000), which is how these are
    actually written."""
    cleaned = _BUDGET_CLEAN_RE.sub("", text)
    matches = list(_BUDGET_NUMBER_RE.finditer(cleaned))
    if not matches:
        return []
    values: List[Optional[float]] = []
    scales: List[Optional[float]] = []
    for match in matches:
        raw = match.group(1)
        if not raw:
            continue
        scale_word = (match.group(2) or "").lower()
        values.append(float(raw))
        scales.append(_SCALE_MULTIPLIERS[scale_word] if scale_word else None)
    if not values:
        return []
    # Back-fill a missing scale from the NEXT number that has one ("40-50
    # lakh": the 40 has no scale word, the 50 does, and both are lakhs).
    resolved: List[float] = []
    for index, value in enumerate(values):
        scale = scales[index]
        if scale is None:
            following = next((s for s in scales[index + 1 :] if s is not None), None)
            preceding = next((s for s in reversed(scales[:index]) if s is not None), None)
            scale = following if following is not None else preceding
        resolved.append(value * scale if scale is not None else value)
    return resolved


def _normalize_ranges(requirement: StructuredRequirement) -> None:
    """Guards the one thing a min/max pair can get wrong on its way out of a
    language model: the two ends arriving the wrong way round. Swapped
    rather than dropped — both numbers are real, only their order is
    mistaken, and dropping one would lose information the message actually
    carried."""
    if (
        requirement.budget_min_inr is not None
        and requirement.budget_max_inr is not None
        and requirement.budget_min_inr > requirement.budget_max_inr
    ):
        requirement.budget_min_inr, requirement.budget_max_inr = (
            requirement.budget_max_inr,
            requirement.budget_min_inr,
        )
    if (
        requirement.carpet_area_min is not None
        and requirement.carpet_area_max is not None
        and requirement.carpet_area_min > requirement.carpet_area_max
    ):
        requirement.carpet_area_min, requirement.carpet_area_max = (
            requirement.carpet_area_max,
            requirement.carpet_area_min,
        )


def _sanitize_listing_type(requirement: StructuredRequirement) -> None:
    """Deterministic safety net over the LLM's Sale/Rent call, run only when
    the source message held exactly ONE requirement — for a multi-requirement
    message, message_text covers every requirement in it, so a rent word
    anywhere in the text could belong to a completely different line and
    would wrongly flip an unrelated Sale requirement. Same reasoning, and
    same one-directional behaviour, as the property stage's version."""
    if requirement.listing_type == "Rent":
        return
    text = requirement.message_text or ""
    if _RENT_SIGNAL_RE.search(text) and not _SALE_SIGNAL_RE.search(text):
        requirement.listing_type = "Rent"
