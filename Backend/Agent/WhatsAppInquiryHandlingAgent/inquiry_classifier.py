"""LLM-driven classification stage (Step 3 of the inquiry-handling
pipeline): decides whether one user's already-debounced batch of WhatsApp
messages is a property-related inquiry or casual/unrelated chatter (a
greeting, "happy birthday", a wrong number, etc.).

Genuinely agentic (LLM-driven) code — belongs in Agent/, not Service/
(mirrors Agent/WhatsAppDataFetchingAgent/property_structurer.py).

Runs on GLM-4.7-FlashX via Z.ai (Config/settings.py's zai_inquiry_model),
the same transport the property and requirement structuring stages use
(Agent/WhatsAppDataFetchingAgent/glm_client.py) but on its OWN Z.ai account
and API key (ZAI_API_KEY_INQUIRY) — previously ran on Gemini, then shared the
property key. Z.ai's concurrency allowance is per account, so a separate key
means a bulk property import (ZAI_API_KEY_PROPERTY) can never hold up a
client inquiry. Every call still goes through a gate (glm_gate.py), on the
inquiry lane, so two simultaneous requests on this one account are still
serialised; a blank ZAI_API_KEY_INQUIRY falls back to the property key and
lane. A much simpler fixed-shape judgment (one bool + a short reason) than
property/requirement extraction, so FlashX's speed and lower cost apply
cleanly here.

Token-efficiency (requirement #4 from the feature spec): every batch is
classified in exactly ONE request containing only that batch's own text —
never resending earlier batches/history for the same number, never
including anything from another client's conversation, and asking for a
minimal structured-output shape (a bool + a short reason, see
inquiry_classification_schema.py) instead of free-form prose.

Z.ai's API only offers `response_format: json_object` (a loose "valid JSON"
guarantee, not a bound Pydantic schema, unlike Gemini's native
response_schema), so the exact output shape is spelled out in the prompt
instead and validated manually on the way back in — the same pattern
property_structurer.py and requirement_structurer.py already use.
"""

from __future__ import annotations

import json
import re
from typing import List

from pydantic import ValidationError

from Agent.WhatsAppDataFetchingAgent import glm_client, glm_gate
from Agent.WhatsAppInquiryHandlingAgent.inquiry_classification_schema import InquiryClassification
from Config.settings import get_settings
from Middleware import step_logger
from Model.WhatsAppInquiryHandlingModel.inquiry_message import InquiryChatMessage

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def classify_batch(messages: List[InquiryChatMessage]) -> InquiryClassification:
    """Classifies one user's already-buffered batch. Fails safe: any error
    (missing/bad key, network failure, unparseable response) returns
    is_property_related=False rather than raising — for a business-critical
    pipeline, silently missing one inquiry (the user can always follow up
    again) is a far smaller failure than crashing the batch handler or
    letting a malformed LLM response start the wrong downstream flow."""
    combined_text = "\n".join(m.text.strip() for m in messages if m.text.strip())
    if not combined_text:
        return InquiryClassification(is_property_related=False, reason="empty batch")

    if not glm_client.has_api_key(glm_gate.LANE_INQUIRY):
        step_logger.error(
            "ZAI_API_KEY_INQUIRY is not set (and there is no ZAI_API_KEY_PROPERTY to fall back to) — "
            "add it to Backend/.env before inquiry classification can run."
        )
        return InquiryClassification(is_property_related=False, reason="ZAI_API_KEY_INQUIRY not configured")

    request_body = glm_client.build_request_body(_build_system_prompt(), _build_user_prompt(combined_text), max_tokens=300)
    request_body["model"] = get_settings().zai_inquiry_model

    failure: dict = {}
    content = glm_client.post_with_retries(
        request_body,
        "an inquiry classification batch",
        site="intent",
        failure=failure,
        # A person is waiting on this reply right now, unlike a background
        # property/requirement batch — jump the gate's queue so a bulk
        # import never makes an inquiry wait behind the whole backlog. See
        # glm_gate.py's "Priority lane" note.
        priority=True,
        # Runs on its own Z.ai account (ZAI_API_KEY_INQUIRY) and its own gate,
        # so a bulk property import can no longer hold the slot this call
        # needs. Falls back to the property lane while that key is blank —
        # see glm_client.lane_for.
        lane=glm_gate.LANE_INQUIRY,
    )
    if content is None:
        reason = failure.get("reason") or "classification request failed"
        step_logger.error(f"GLM inquiry-classification request failed: {reason}")
        return InquiryClassification(is_property_related=False, reason=reason)

    return _parse_classification(content)


def _build_system_prompt() -> str:
    return (
        "You are classifying a WhatsApp message (or a short burst of messages sent "
        "seconds apart by the same person) received by a real estate agency.\n\n"
        "Decide: is this person expressing interest in, or asking about, buying, "
        "renting, selling, or otherwise inquiring about a property? Answer true only "
        "for a genuine property inquiry. Answer false for greetings (\"hello\", \"hi\"), "
        "small talk, wrong numbers, spam, birthday/festival wishes, or anything else "
        "unrelated to property.\n\n"
        "Reply with ONLY a single JSON object, no other text and no markdown code "
        'fence, shaped exactly like: {"is_property_related": true or false, "reason": '
        '"one short phrase explaining the decision, e.g. \\"greeting only\\" or '
        '\\"asks for a rented villa\\""}. "reason" may be null if there is nothing worth '
        "noting."
    )


def _build_user_prompt(combined_text: str) -> str:
    return f'Message(s):\n"""\n{combined_text}\n"""'


def _parse_classification(content: str) -> InquiryClassification:
    cleaned = _CODE_FENCE_RE.sub("", content.strip()).strip()
    try:
        raw = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        step_logger.error(f"GLM inquiry-classification response was not valid JSON: {exc}")
        return InquiryClassification(is_property_related=False, reason="unparseable model response")

    try:
        return InquiryClassification.model_validate(raw)
    except ValidationError as exc:
        step_logger.error(f"GLM inquiry-classification response didn't match the expected schema: {exc}")
        return InquiryClassification(is_property_related=False, reason="unparseable model response")
