"""Owns the "back half" of the REQUIREMENT pipeline: receiving flushed
message batches from the requirement buffering stage (the second
MessageBufferService instance in whatsapp_service.py), running them through
the LLM structuring stage (Agent/BrokerRequirementAgent/
requirement_structurer.py), and storing the result
(Service/BrokerRequirementService/requirement_store.py) for the
Controller layer to read.

Intentionally the short version of property_pipeline_service.py. That module
has to embed, area-file and review-flag every property it produces; this one
stores what the LLM returned and stops.

EXACT RE-POSTS are dropped once, at the front, on the raw message text —
the same approach the property pipeline takes (see _drop_duplicate_messages).
A broker re-posting the identical requirement text costs one indexed
fingerprint lookup for the whole batch instead of an LLM call and a second
set of stored rows. A re-post whose text differs by more than case/whitespace
is deliberately NOT caught: two brokers asking for the same thing in their
own words are two real requirements.

The stages it deliberately does NOT have:

  - NO embedding. Nothing searches requirements by vector similarity.
  - NO semantic duplicate detection. Beyond the exact-text check above,
    two brokers asking for the same thing are two real requirements, not a
    duplicate to resolve.
  - NO area matching / knowledge-base side channel. A requirement's areas
    are copied as written and never judged against the client's selected
    areas, so there is no Main/Outsider split to make and nothing to file.
  - NO review queue. A requirement is stored, shown, editable and
    deletable. That is its entire lifecycle.

A message the LLM reads as a LISTING rather than a demand (the keyword filter
matched it on wording alone) is not dropped: it is handed to the property
buffer (see _forward_to_property_pipeline), exactly as the property pipeline
hands demands over to this one.

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

import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from Agent.BrokerRequirementAgent import requirement_structurer
from Database.broker_requirement_repository import EDITABLE_CONTENT_FIELDS
from Middleware import step_logger
from Model.BrokerRequirementModel.broker_requirement import (
    BrokerRequirementRecord,
    StructuredRequirement,
)
from Model.WhatsAppDataFetchingModel.whatsapp_message import WhatsAppChatMessage
from Service.BackendUsageService import cpu_usage_service
from Service.BrokerRequirementService import requirement_store
from Service.WhatsAppDataFetchingService import (
    display_settings_service,
    message_fingerprint,
    pending_batch_store,
    timestamp_formatting,
)

# Fingerprints of messages some batch is structuring RIGHT NOW (claimed in
# _drop_duplicate_messages, released once that batch has finished storing).
# Every flush runs on its own thread (see message_buffer_service.py), so two
# batches can overlap; without this, the same text in both would pass the
# stored-fingerprint lookup twice — neither copy stored yet — and be
# structured and stored twice.
_in_flight_lock = threading.Lock()
_in_flight_fingerprints: Set[str] = set()


@cpu_usage_service.tracked("Requirement batch — LLM structuring, save & matching", "WhatsApp → Requirements")
def handle_batch_ready(batch: List[WhatsAppChatMessage]) -> None:
    """Called by the requirement buffering stage whenever a batch is flushed
    (10 messages gathered, or the batch window elapsed). Already runs on its
    own thread (see message_buffer_service.py), so the blocking LLM call
    here never stalls WhatsApp message capture.

    Never raises, and never loses the batch: this is a thread entry point,
    and an exception escaping it would be swallowed by the thread with no
    trace anywhere useful — and would take every captured message in the
    batch with it.

    The batch is CLAIMED on disk for the whole of this call and finished with
    exactly one of release() (done — the record and its messages are removed,
    leaving the queue clear for the next batch) or defer() (try again later).
    The claim is usually the record the buffering stage already made at
    hand-off, so the messages are covered from the moment they were captured
    to the moment they are processed, with no gap in between and nothing left
    behind afterwards. Re-running a batch later is safe because
    _drop_duplicate_messages already skips text this pipeline has stored, so
    whatever did get through the first time is not redone."""
    claimed: Set[str] = set()
    original_batch = list(batch)
    batch_id = pending_batch_store.claim(pending_batch_store.REQUIREMENT_PIPELINE, original_batch)
    retry_reason: Optional[str] = None
    try:
        received_count = len(batch)
        batch, claimed = _drop_duplicate_messages(batch)
        if not batch:
            step_logger.success(
                f"Requirement batch processed: all {received_count} message(s) were text this pipeline has "
                "already structured before — nothing sent to GLM"
            )
            return

        step_logger.step(f"Sending batch of {len(batch)} requirement message(s) to GLM for structuring")
        # See requirement_structurer.structure_batch: this is how a batch
        # that produced nothing because GLM never answered is told apart from
        # one that produced nothing because none of its messages were
        # requirements. The first must be retried; the second is a finished,
        # correct batch.
        outcome: Dict[str, Any] = {}
        requirements, property_messages = requirement_structurer.structure_batch_with_routing(
            batch, outcome=outcome
        )

        if outcome.get("llm_failed"):
            step_logger.error(
                f"Requirement batch of {len(batch)} message(s) could not be structured: "
                f"{outcome.get('reason')}. It is queued for retry — none of these messages have been "
                "dropped."
            )
            retry_reason = str(outcome.get("reason") or "the GLM call failed")
            return

        _hold_unanswered_messages(batch, outcome)

        requirement_store.add_requirements(requirements)
        stored = len(requirements)

        # After the requirements are safely stored, not before: if storing
        # raised, this whole batch is retried (LLM call included), and
        # forwarding first would hand the same listing to the property
        # pipeline twice. These messages produced no requirement at all (see
        # requirement_structurer.structure_batch_with_routing), so nothing
        # here depends on them.
        _forward_to_property_pipeline(property_messages)

        step_logger.success(
            f"Requirement batch processed: {stored} requirement{'' if stored == 1 else 's'} stored "
            f"out of {len(batch)} message(s)"
            + (
                f" ({received_count - len(batch)} skipped as already-seen text)"
                if received_count != len(batch)
                else ""
            )
        )
        _store_matches_for_new_requirements(requirements)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(
            f"Requirement batch of {len(original_batch)} message(s) failed unexpectedly ({exc!r}) — "
            "holding it for retry rather than dropping it."
        )
        retry_reason = f"the batch handler raised: {exc!r}"
    finally:
        # Only after storing has finished (or failed): from here on, a later
        # batch's stored-fingerprint lookup is what recognises this text.
        if claimed:
            with _in_flight_lock:
                _in_flight_fingerprints.difference_update(claimed)
        # Exactly one of these, always, however this function was left —
        # including the early `return` above, which is why it lives here.
        if retry_reason is None:
            pending_batch_store.release(batch_id)
        else:
            pending_batch_store.defer(
                batch_id, pending_batch_store.REQUIREMENT_PIPELINE, original_batch, retry_reason
            )


def _hold_unanswered_messages(batch: List[WhatsAppChatMessage], outcome: Dict[str, Any]) -> None:
    """A batch can come back PARTIALLY answered: GLM returns verdicts for
    most of the messages and simply never mentions the rest. The answered
    ones are stored normally; the rest used to be logged as "dropped from
    this batch" and that was genuinely the end of them.

    Now they are queued as a small batch of their own — only the messages
    that got no verdict, never the whole batch, so nothing that DID succeed
    is redone. A re-ask about one or two messages on their own is also the
    case the model is most likely to answer completely, so this normally
    clears on the first retry."""
    unanswered_ids = set(outcome.get("unanswered_message_ids") or [])
    if not unanswered_ids:
        return
    messages = [message for message in batch if message.message_id in unanswered_ids]
    if not messages:
        return
    pending_batch_store.enqueue(
        pending_batch_store.REQUIREMENT_PIPELINE,
        messages,
        f"GLM returned no verdict at all for {len(messages)} of the {len(batch)} message(s) in this batch",
    )


def _forward_to_property_pipeline(messages: List[WhatsAppChatMessage]) -> None:
    """Hands the messages the LLM identified as LISTINGS (PART 1's
    is_property_listing) over to the property pipeline — the mirror image of
    property_pipeline_service._forward_to_requirement_pipeline.

    They go into the property BUFFER rather than straight into a property
    LLM call, so they ride along with whatever that buffer is already
    collecting and share a batch with it — one re-routed message must not
    buy its own API call.

    The import is local because whatsapp_service imports THIS module at
    module level. Swallowed broadly: this is the tail end of a batch whose
    requirements are already stored, and a re-route failure must never turn
    that into a failed batch."""
    if not messages:
        return
    try:
        from Service.WhatsAppDataFetchingService import whatsapp_service

        whatsapp_service.enqueue_property_messages(messages)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(
            f"Could not re-route {len(messages)} listing message(s) to the property pipeline "
            f"(the requirements in this batch are unaffected): {exc!r}"
        )
        # These are real listings this stage has already decided are NOT
        # requirements, so nothing else will ever look at them again —
        # logging and moving on would end them here. Held for the property
        # pipeline to pick up instead, like any other batch it could not
        # process.
        pending_batch_store.enqueue(
            pending_batch_store.PROPERTY_PIPELINE,
            messages,
            f"could not be re-routed from the requirement stage: {exc!r}",
        )


def _store_matches_for_new_requirements(requirements: List[StructuredRequirement]) -> None:
    """Scores the requirements this batch just stored and stores their
    matches (see requirement_matching_service.score_new_requirements) — one
    write for the whole batch.

    Runs only AFTER the requirements themselves are safely stored, and is
    swallowed on failure: matching is a by-product here, and a matching
    error must never be reported as the batch having failed. A requirement
    whose matches could not be stored now is simply scored the first time
    its matches are opened. Lazy import for the same reason delete_requirement
    uses one."""
    if not requirements:
        return
    try:
        from Service.BrokerRequirementService import requirement_matching_service

        stored = requirement_matching_service.score_new_requirements(requirements)
        step_logger.info(
            f"[Matching] Stored {stored} property match(es) for {len(requirements)} new requirement(s)."
        )
    except Exception as exc:  # noqa: BLE001
        step_logger.error(
            f"[Matching] Could not store matches for {len(requirements)} new requirement(s) (the requirements "
            f"are stored; they will be scored when opened): {exc!r}"
        )


def _drop_duplicate_messages(batch: List[WhatsAppChatMessage]) -> Tuple[List[WhatsAppChatMessage], Set[str]]:
    """Filters out every message whose text this pipeline has already turned
    into requirements, BEFORE the LLM stage runs. Returns the messages to
    structure, plus the fingerprints this batch claimed as in-flight (which
    the caller must release when it is done).

    Three things are checked, because none covers the others:
      - within THIS batch, so the same text forwarded twice (e.g. received
        by two linked numbers in the same group) is structured once;
      - against batches running concurrently right now (see
        _in_flight_fingerprints);
      - against what is already stored, via ONE indexed fingerprint query
        for the whole batch (Service/WhatsAppDataFetchingService/
        message_fingerprint.py) — no stored message text is ever loaded or
        compared.

    The in-flight claim is taken BEFORE the stored lookup, not after: a
    concurrent batch that releases its claim has by then already committed
    its rows, so this batch's lookup is guaranteed to see them.

    A message whose text has no fingerprint (blank/whitespace-only after
    normalization) is always kept: there is nothing to match it on. If the
    stored lookup itself fails, the batch is structured anyway rather than
    dropped — this only ever skips a message it positively recognises."""
    kept: List[WhatsAppChatMessage] = []
    claimed: Set[str] = set()
    pending: List[Tuple[WhatsAppChatMessage, Optional[str]]] = []
    seen_in_batch: Dict[str, str] = {}

    with _in_flight_lock:
        for message in batch:
            if not message_fingerprint.is_fingerprintable(message.text):
                pending.append((message, None))
                continue
            text_fingerprint = message_fingerprint.fingerprint(message.text)

            earlier_in_batch = seen_in_batch.get(text_fingerprint)
            if earlier_in_batch is not None:
                step_logger.info(
                    f"Requirement message {message.message_id!r} is the same text as {earlier_in_batch!r}, "
                    "earlier in this same batch — structuring it once instead of twice."
                )
                continue
            if text_fingerprint in _in_flight_fingerprints:
                step_logger.info(
                    f"Requirement message {message.message_id!r} is the same text another batch is structuring "
                    "right now — skipped before the LLM stage."
                )
                continue

            seen_in_batch[text_fingerprint] = message.message_id
            _in_flight_fingerprints.add(text_fingerprint)
            claimed.add(text_fingerprint)
            pending.append((message, text_fingerprint))

    try:
        already_stored = requirement_store.find_message_ids_by_fingerprints(claimed) if claimed else {}
    except Exception as exc:  # noqa: BLE001
        step_logger.warn(
            f"Could not check {len(claimed)} requirement message(s) against already-stored text ({exc!r}) — "
            "structuring them anyway rather than risk dropping a real requirement."
        )
        already_stored = {}

    for message, text_fingerprint in pending:
        stored_message_id = already_stored.get(text_fingerprint) if text_fingerprint is not None else None
        if stored_message_id is not None:
            step_logger.info(
                f"Requirement message {message.message_id!r} is an exact re-post of already-processed message "
                f"{stored_message_id!r} — skipped before the LLM stage, so it costs nothing to structure."
            )
            continue
        kept.append(message)

    return kept, claimed


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
    if updated is None:
        return None
    # The edit may have changed what the requirement asks for, so its stored
    # matches are re-scored now rather than left describing the old version.
    # Swallowed for the same reason as delete_requirement's cache eviction: a
    # matching failure must never turn a saved edit into an error (and the
    # next time the matches are opened, the changed text is detected and
    # they are re-scored anyway).
    try:
        from Service.BrokerRequirementService import requirement_matching_service

        requirement_matching_service.recompute_for_requirement(record_id)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"[Matching] Could not re-score requirement {record_id} after its edit: {exc!r}")
    return _to_record(updated)


def delete_requirement(record_id: str) -> bool:
    deleted = requirement_store.delete_requirement(record_id)
    if deleted:
        # Drop the memoised requirement embedding (and, without a database,
        # its stored matches — with one, the delete above already cascaded
        # them away) so a deleted record_id leaves nothing behind it. Lazy
        # import + broad except for
        # the same reason client_store.upsert_client uses one for matching:
        # this is the only place the data-fetching feature touches the
        # matching feature at all, and a cache-eviction failure must never
        # turn a successful delete into an error.
        try:
            from Service.BrokerRequirementService import requirement_matching_service

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
