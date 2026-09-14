"""LLM-driven classification stage (Step 3 of the inquiry-handling
pipeline): decides whether one user's already-debounced batch of WhatsApp
messages is a property-related inquiry or casual/unrelated chatter (a
greeting, "happy birthday", a wrong number, etc.).

Genuinely agentic (LLM-driven) code — belongs in Agent/, not Service/
(mirrors Agent/WhatsAppDataFetchingAgent/property_structurer.py).

Token-efficiency (requirement #4 from the feature spec): every batch is
classified in exactly ONE request containing only that batch's own text —
never resending earlier batches/history for the same number, never
including anything from another client's conversation, and asking for a
minimal structured-output shape (a bool + a short reason, see
inquiry_classification_schema.py) instead of free-form prose.
"""

from __future__ import annotations

from typing import List, Optional

from google import genai
from google.genai import types

from Agent.WhatsAppInquiryHandlingAgent.inquiry_classification_schema import InquiryClassification
from Config.settings import get_settings
from Middleware import step_logger
from Model.WhatsAppInquiryHandlingModel.inquiry_message import InquiryChatMessage
from Service.LLMUsageService import llm_usage_service

_client: Optional[genai.Client] = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = get_settings().gemini_api_key
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set — add it to Backend/.env before inquiry classification can run."
            )
        _client = genai.Client(api_key=api_key)
    return _client


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

    model = get_settings().gemini_model
    try:
        client = _get_client()
        response = client.models.generate_content(
            model=model,
            contents=_build_prompt(combined_text),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=InquiryClassification,
                temperature=0.0,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Gemini inquiry-classification request failed: {exc!r}")
        return InquiryClassification(is_property_related=False, reason=f"classification request failed: {exc!r}")

    _record_usage(model, response)
    return _parse_classification(response)


def _record_usage(model: str, response) -> None:
    """Best-effort: feeds this call's token counts to the Dashboard's LLM
    Cost tab (Service/LLMUsageService/llm_usage_service.py). Never raises —
    a tracking failure must never affect a real classification. The
    google-genai SDK exposes this directly on the response as
    `usage_metadata` (no request-shape change needed, unlike the streamed
    GLM call sites)."""
    try:
        usage = getattr(response, "usage_metadata", None)
        input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0) if usage is not None else 0
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0) if usage is not None else 0
        llm_usage_service.observe_call("intent", model, input_tokens, output_tokens)
    except Exception as exc:  # noqa: BLE001
        step_logger.warn(f"Could not record LLM usage for an inquiry-classification call: {exc!r}")


def _build_prompt(combined_text: str) -> str:
    return (
        "You are classifying a WhatsApp message (or a short burst of messages sent "
        "seconds apart by the same person) received by a real estate agency.\n\n"
        "Decide: is this person expressing interest in, or asking about, buying, "
        "renting, selling, or otherwise inquiring about a property? Answer true only "
        "for a genuine property inquiry. Answer false for greetings (\"hello\", \"hi\"), "
        "small talk, wrong numbers, spam, birthday/festival wishes, or anything else "
        "unrelated to property.\n\n"
        f'Message(s):\n"""\n{combined_text}\n"""'
    )


def _parse_classification(response) -> InquiryClassification:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, InquiryClassification):
        return parsed

    # Fall back to manual parsing if the SDK couldn't auto-parse (e.g. the
    # model's output didn't quite match the schema).
    raw_text = getattr(response, "text", None)
    if raw_text:
        try:
            return InquiryClassification.model_validate_json(raw_text)
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"Could not parse Gemini classification response: {exc!r}")

    step_logger.error("Gemini returned no usable content for an inquiry classification request.")
    return InquiryClassification(is_property_related=False, reason="unparseable model response")
