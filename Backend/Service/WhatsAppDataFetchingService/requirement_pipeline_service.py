"""Owns the "back half" of the REQUIREMENT pipeline: receiving flushed
message batches from the requirement buffering stage (the second
MessageBufferService instance in whatsapp_service.py), running them through
the LLM structuring stage (Agent/WhatsAppDataFetchingAgent/
requirement_structurer.py), and storing the result
(Service/WhatsAppDataFetchingService/requirement_store.py) for the
Controller layer to read.

Intentionally the short version of property_pipeline_service.py. That module
has to embed, duplicate-check, area-file and review-flag every property it
produces; this one stores what the LLM returned and stops. The four stages
it deliberately does NOT have:

  - NO embedding. Nothing searches requirements by vector similarity.
  - NO duplicate detection. Two brokers asking for the same thing are two
    real requirements, not a duplicate to resolve — and the whole
    HIGH_CONFIDENCE/UNCERTAIN review apparatus exists to protect a listing
    from being silently lost, which does not apply to a demand.
  - NO area matching / knowledge-base side channel. A requirement's areas
    are copied as written and never judged against the client's selected
    areas, so there is no Main/Outsider split to make and nothing to file.
  - NO review queue. A requirement is stored, shown, editable and
    deletable. That is its entire lifecycle.

What it DOES share with the property pipeline, exactly: the batching
contract. A batch arrives here when 10 qualifying messages have accumulated
OR the batch window has elapsed since the first message of the batch,
whichever comes first, and the window timer resets on every flush — the same
MessageBufferService class, the same batch size, the same
`batch_window_minutes` setting. One WhatsApp message can also produce
several requirement records here, each stored as its own row, exactly as a
message can produce several properties.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from Agent.WhatsAppDataFetchingAgent import requirement_structurer
from Database.broker_requirement_repository import EDITABLE_CONTENT_FIELDS
from Middleware import step_logger
from Model.WhatsAppDataFetchingModel.broker_requirement import (
    BrokerRequirementRecord,
    StructuredRequirement,
)
from Model.WhatsAppDataFetchingModel.whatsapp_message import WhatsAppChatMessage
from Service.WhatsAppDataFetchingService import (
    display_settings_service,
    requirement_store,
    timestamp_formatting,
)


def handle_batch_ready(batch: List[WhatsAppChatMessage]) -> None:
    """Called by the requirement buffering stage whenever a batch is flushed
    (10 messages gathered, or the batch window elapsed). Already runs on its
    own thread (see message_buffer_service.py), so the blocking LLM call
    here never stalls WhatsApp message capture.

    Never raises: this is a thread entry point, and an exception escaping it
    would be swallowed by the thread with no trace anywhere useful."""
    try:
        step_logger.step(f"Sending batch of {len(batch)} requirement message(s) to GLM for structuring")
        requirements = requirement_structurer.structure_batch(batch)

        stored = 0
        for requirement in requirements:
            requirement_store.add_requirement(requirement)
            stored += 1

        step_logger.success(
            f"Requirement batch processed: {stored} requirement{'' if stored == 1 else 's'} stored "
            f"out of {len(batch)} message(s)"
        )
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Requirement batch failed and was dropped: {exc!r}")


def get_requirements(limit: int = 500) -> List[BrokerRequirementRecord]:
    """Backs the Broker Requirements page's list. Unlike the properties list
    there is no photo-less "summary" variant to worry about — a requirement
    row carries no blobs, so the full row IS the cheap row."""
    return [_to_record(requirement) for requirement in requirement_store.get_all_requirements(limit=limit)]


def get_requirement(record_id: str) -> Optional[BrokerRequirementRecord]:
    requirement = requirement_store.get_requirement(record_id)
    return _to_record(requirement) if requirement is not None else None


def get_requirement_count() -> int:
    return requirement_store.get_requirement_count()


def get_requirements_version() -> str:
    return requirement_store.get_requirements_version()


def update_requirement(record_id: str, content_updates: Dict[str, Any]) -> Optional[BrokerRequirementRecord]:
    """Backs the Broker Requirements page's Edit dialog. Only the content
    fields a human is allowed to change are applied — the WhatsApp metadata
    (sender, group, original message, timestamp) is the audit trail and is
    never editable. Returns None if no requirement with this record_id
    exists."""
    filtered = {key: value for key, value in content_updates.items() if key in EDITABLE_CONTENT_FIELDS}
    if not filtered:
        # Nothing editable was sent — return the record unchanged rather
        # than writing an empty update (which would still bump updated_at
        # and make every polling page re-fetch for no reason).
        return get_requirement(record_id)
    updated = requirement_store.update_requirement(record_id, filtered)
    return _to_record(updated) if updated is not None else None


def delete_requirement(record_id: str) -> bool:
    deleted = requirement_store.delete_requirement(record_id)
    if deleted:
        # Drop the memoised requirement embedding so a deleted record_id
        # leaves nothing cached behind it. Lazy import + broad except for
        # the same reason client_store.upsert_client uses one for matching:
        # this is the only place the data-fetching feature touches the
        # matching feature at all, and a cache-eviction failure must never
        # turn a successful delete into an error.
        try:
            from Service.ClientPropertyMatchingService import requirement_matching_service

            requirement_matching_service.forget_requirement(record_id)
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"[Matching] Could not clear the cached vector for {record_id}: {exc!r}")
    return deleted


def _to_record(requirement: StructuredRequirement) -> BrokerRequirementRecord:
    use_24_hour_format = display_settings_service.get_use_24_hour_format()
    return BrokerRequirementRecord(
        **requirement.model_dump(),
        formatted_timestamp=timestamp_formatting.format_ist(requirement.message_timestamp, use_24_hour_format),
    )
