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
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from Agent.BrokerRequirementAgent import requirement_normalization, requirement_structurer
from Database.broker_requirement_repository import EDITABLE_CONTENT_FIELDS
from Middleware import step_logger
from Model.record_source import SOURCE_MANUAL
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

# The editable requirement fields no score is built from — the broker's own
# name and number, and the staff-only `notes` catch-all (StructuredRequirement.
# notes — see requirement_matching_service._as_pseudo_client, which never
# reads it). Every other editable field reaches the scored text (see
# requirement_matching_service._as_pseudo_client and
# client_requirement_text_builder.build_requirement_text), so changing one
# genuinely changes what this requirement asks for and its matches are
# re-scored. These do not, so an edit confined to them leaves the stored
# shortlist exactly as it stands. The listing-side twin of this rule lives in
# match_invalidation_service.MATCH_NEUTRAL_FIELDS.
MATCH_NEUTRAL_REQUIREMENT_FIELDS = frozenset({"contact_name", "contact_phones", "notes"})


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


_MAX_SIZE_TEXT_LENGTH = 80


def _align_property_sizes(sizes: Optional[dict], requirement_type: Optional[str]) -> Optional[dict]:
    """{type: size} keyed by the types this requirement actually asks for,
    in the order they are asked for — or None when nothing is left.

    Two jobs, both of which exist because a size is looked up BY its type
    name later (Service/ClientPropertyMatchingService/normalization.size_for):

      - a size whose type is no longer picked is dropped. Un-ticking a type
        must not leave its size behind on the record;
      - a size keyed by a name the type has since been renamed to something
        else ("Apartment" -> "Flat", "Land/Plot" -> "Plot", see
        requirement_normalization.canonical_requirement_type) is re-keyed
        onto the stored name. Each key is put through the same
        canonicalization the type list itself went through, so the two
        always agree however the key was written.

    The identical rule the client side applies to a client's own sizes (see
    inquiry_form_service._clean_property_sizes), and for the identical
    reason. Blank sizes are dropped — an unanswered optional box is not a
    preference."""
    if not sizes:
        return None
    labels = [label for label in (part.strip() for part in (requirement_type or "").split(",")) if label]
    if not labels:
        return None
    by_lower = {label.lower(): label for label in labels}
    aligned: Dict[str, str] = {}
    for key, value in sizes.items():
        text = " ".join(str(value or "").split())[:_MAX_SIZE_TEXT_LENGTH]
        if not text:
            continue
        written = " ".join(str(key).split())
        if not written:
            continue
        # The key as sent first, then the canonical form of it — a key that
        # already matches a stored label must not be put through a rename
        # that could split it into two.
        candidates = [written]
        canonical = requirement_normalization.canonical_requirement_type(written)
        if canonical:
            candidates.extend(part.strip() for part in canonical.split(","))
        for candidate in candidates:
            label = by_lower.get(candidate.lower())
            if label and label not in aligned:
                aligned[label] = text
                break
    # Back into the order the types are asked in, so the dict reads the same
    # way the type list does.
    return {label: aligned[label] for label in labels if label in aligned} or None


def create_requirement(content_fields: Dict[str, Any]) -> BrokerRequirementRecord:
    """Backs the Broker Requirements page's Add dialog — a requirement entered
    by hand (an operator hears what a broker wants on a call, or in a chat
    this app does not monitor) rather than structured out of a captured
    WhatsApp message. The manual counterpart of
    property_pipeline_service.create_property, and deliberately built the same
    way: every WhatsApp-metadata field StructuredRequirement otherwise
    requires (sender, group, message text/timestamp) gets the same kind of
    placeholder, since none of it exists for a manual entry, and every content
    field is optional, matching the dialog itself.

    No LLM call and no message batching happen here — there is no message to
    structure. The only work is the same deterministic tidy-up the structuring
    stage applies AFTER the model answers, so a hand-typed "flat" and "80L-1cr"
    are stored exactly as the pipeline would have stored them and therefore
    filter, sort and match identically.

    Database cost: one transaction to store it (the placeholder message row
    plus the requirement row, as for any batch) and one to store its matches.
    Nothing is re-read afterwards — the record returned is built from the
    object just written."""
    fields = {
        key: value
        for key, value in content_fields.items()
        # None means "not given" for a NEW requirement, so it is dropped and
        # the model's own default applies (this is what keeps an omitted
        # listing_type at "Sale" rather than failing validation on None).
        if key in EDITABLE_CONTENT_FIELDS and value is not None
    }
    # Same rule the Edit dialog follows: the Area column's value is simply the
    # first preferred area, so the two can never disagree. A lone area_name
    # with no list behind it becomes that list.
    areas = [str(area).strip() for area in (fields.pop("preferred_areas", None) or []) if str(area).strip()]
    area_name = fields.pop("area_name", None)
    if areas:
        area_name = areas[0]
    elif area_name:
        areas = [area_name]

    description = fields.get("description")
    requirement = StructuredRequirement(
        source_message_id=f"manual-{uuid.uuid4().hex}",
        # Typed in by a member of staff on the Broker Requirements page —
        # see StructuredRequirement.source.
        source=SOURCE_MANUAL,
        area_name=area_name,
        preferred_areas=areas,
        group_name="Manually added",
        chat_type="personal",
        sender_name="Manual entry",
        sender_saved_name="Manual entry",
        sender_phone="manual",
        message_text=description or "Added manually via the Broker Requirements page.",
        message_timestamp=datetime.now(timezone.utc),
        **fields,
    )
    requirement.bhk = requirement_normalization.canonical_bhk(requirement.bhk)
    requirement.requirement_type = requirement_normalization.canonical_requirement_type(requirement.requirement_type)
    requirement.furnishing = requirement_normalization.canonical_furnishing(requirement.furnishing)
    # AFTER the line above, deliberately: canonicalizing renames the types
    # ("Apartment" -> "Flat", "Land/Plot" -> "Plot"), and a size keyed by the
    # name the dialog sent would then belong to a type that is no longer
    # there — stored, but never found again by the matcher, which looks sizes
    # up BY the stored type name (normalization.size_for).
    requirement.property_sizes = _align_property_sizes(
        requirement.property_sizes, requirement.requirement_type
    )
    # The structurer's own post-model clean-ups, reused rather than repeated:
    # both are pure functions over the record (no LLM call, no I/O), and this
    # is what lets someone type just "80L-1cr" into Budget and still get the
    # numeric ends every filter and the matching gate read.
    requirement_structurer._fill_missing_budget_amounts(requirement)
    requirement_structurer._normalize_budget_range(requirement)

    requirement_store.add_requirements([requirement])
    step_logger.success(f"Requirement {requirement.record_id} added by hand from the Broker Requirements page")
    _store_matches_for_new_requirements([requirement])
    return _to_record(requirement)


def update_requirement(record_id: str, content_updates: Dict[str, Any]) -> Optional[BrokerRequirementRecord]:
    """Backs the Broker Requirements page's Edit dialog. Only the content
    fields a human is allowed to change are applied — the WhatsApp metadata
    (sender, group, original message, timestamp) is the audit trail and is
    never editable. Returns None if no requirement with this record_id
    exists."""
    filtered = {key: value for key, value in content_updates.items() if key in EDITABLE_CONTENT_FIELDS}
    if "furnishing" in filtered:
        # Tidied BEFORE the comparison below, so re-saving the same level
        # written slightly differently is not counted as a change and does
        # not trigger a re-score. The dialog offers exactly the three
        # canonical values, so in practice this only catches a value that
        # arrived some other way.
        filtered["furnishing"] = requirement_normalization.canonical_furnishing(filtered["furnishing"])
    if not filtered:
        # Nothing editable was sent — return the record unchanged rather
        # than writing an empty update (which would still bump updated_at
        # and make every polling page re-fetch for no reason).
        return get_requirement(record_id)
    # Read BEFORE the write, so the values that actually moved can be told
    # apart from the ones the dialog simply re-posted: its Save sends the
    # whole form every time (RequirementFormDialog.tsx's toPayload), so the
    # keys that arrived say nothing about what a person changed. One
    # primary-key read per manual edit, against a full re-score of every
    # candidate property that this then avoids on a contact-only correction.
    existing = requirement_store.get_requirement(record_id)
    if existing is None:
        return None
    if "property_sizes" in filtered:
        # Tidied BEFORE the comparison below, for the same reason furnishing
        # is: a dict that would be stored identically must not count as a
        # change and must not trigger a needless re-score.
        #
        # An edit is NOT canonicalized (unlike an Add — see
        # create_requirement), so the types are stored exactly as the dialog
        # sent them; the alignment still runs, to drop a size belonging to a
        # type that has just been un-ticked. Keyed against whatever this same
        # save is storing, falling back to what is already on the record when
        # the dialog didn't send a type list at all — no extra read either
        # way, `existing` is already in hand.
        filtered["property_sizes"] = _align_property_sizes(
            filtered["property_sizes"],
            filtered["requirement_type"] if "requirement_type" in filtered else existing.requirement_type,
        )
    moved = {key for key, value in filtered.items() if getattr(existing, key, None) != value}
    updated = requirement_store.update_requirement(record_id, filtered)
    if updated is None:
        return None
    # The edit may have changed what the requirement ASKS FOR, in which case
    # its stored matches describe the old version and are re-scored now.
    # A change to the contact name or number is not that: neither reaches
    # the scored text (requirement_matching_service._as_pseudo_client maps
    # contact_name onto a name nothing embeds, and drops contact_phone
    # entirely), so no score can move and the broker's shortlist must not be
    # torn down and rebuilt to arrive at the same answer.
    #
    # Swallowed for the same reason as delete_requirement's cache eviction: a
    # matching failure must never turn a saved edit into an error (and the
    # next time the matches are opened, the changed text is detected and
    # they are re-scored anyway).
    if moved - MATCH_NEUTRAL_REQUIREMENT_FIELDS:
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
