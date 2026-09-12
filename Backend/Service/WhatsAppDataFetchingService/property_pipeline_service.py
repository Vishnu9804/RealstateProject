"""Owns the "back half" of the property pipeline: receiving flushed message
batches from the buffering stage (Service/WhatsAppDataFetchingService/message_buffer_service.py),
dropping messages whose text has already been processed, running the rest
through the LLM structuring stage (Agent/property_structurer.py) and the
embedding stage (Service/WhatsAppDataFetchingService/embedding_service.py),
then storing the result (Service/WhatsAppDataFetchingService/property_vector_store.py)
for the Controller layer to read.

DUPLICATES are handled once, at the front, on the raw message text rather
than on the extracted properties (see _drop_duplicate_messages). Brokers
re-post a listing by forwarding the identical text, so an exact content
match is the signal that actually occurs in this domain — and catching it
before the LLM runs means a re-post costs one indexed lookup instead of a
structuring call, an embedding, and a scoring pass. A re-post whose text
was edited even slightly is deliberately NOT caught here: it goes through
the normal pipeline and is judged on its extracted fields like anything
else.

Every structured property is embedded exactly once, right here, right after
structuring — the exact vector that ends up in the store, read later only
by client-property match scoring (Service/ClientPropertyMatchingService/
scoring.py). Nothing downstream ever re-embeds or recomputes it.

Not every message in a batch is a listing, and one specific kind of
non-listing is no longer simply dropped: a DEMAND — someone asking FOR a
property rather than offering one ("I want to look for 3bhk flat in vesu").
The cheap keyword filter upstream is built for precision and lets a demand
phrased without its trigger words through to here; the structuring stage now
recognises it by meaning and hands it back (see
_forward_to_requirement_pipeline), which pushes it into the requirement
buffer so it becomes a broker requirement instead of a bogus property. Only
messages that yielded NO property at all can take that path, so it can never
remove anything that would otherwise have been stored here.

needs_review means one thing now: the LLM could barely extract anything
from this property's own text (see Agent/WhatsAppDataFetchingAgent/
property_structurer.py's PART 4 and _apply_information_review). It is a
tiny queue by construction — a property missing a field or two is a normal
property, not a review case — and a flagged property is excluded from
client-property matching entirely, since there is nothing in it to match
on. Nothing is ever discarded: the property is stored with the relevant
part of the original message in `description`, so a human can fill in the
details by hand and file it into Main or Outsider.

needs_review is independent of review_status ("accepted" vs "outsider",
i.e. which of the Main/Outsider tabs a property belongs to) — a property
can arrive here already review_status="outsider", set by the LLM
structuring stage when it falls outside every client-selected area, and
separately be flagged needs_review=True for carrying almost no
information. Both are shown at once; a human resolving the review flag
(see update_property below) picks Main or Outsider explicitly as they do
it (see the Needs review dialog's Move to Main / Move to Outsider actions).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from Agent.WhatsAppDataFetchingAgent import property_structurer
from Database.property_repository import EDITABLE_CONTENT_FIELDS
from Middleware import step_logger
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty
from Model.WhatsAppDataFetchingModel.property_record import PropertyRecord
from Model.WhatsAppDataFetchingModel.structured_property import StructuredProperty
from Model.WhatsAppDataFetchingModel.whatsapp_message import WhatsAppChatMessage
from Service.WhatsAppDataFetchingService import (
    area_knowledge_service,
    display_settings_service,
    embedding_service,
    message_fingerprint,
    property_vector_store,
    timestamp_formatting,
)

_duplicate_message_count = 0
_needs_review_count = 0
_outsider_count = 0

_NON_API_FIELDS = {"embedding", "embedding_model"}


def handle_batch_ready(batch: List[WhatsAppChatMessage]) -> None:
    """Called by the buffering stage whenever a batch is flushed (10
    messages gathered, or 1 hour elapsed). Already runs on its own thread
    (see message_buffer_service.py), so the blocking GLM call here never
    stalls WhatsApp message capture."""
    global _needs_review_count, _outsider_count

    received_count = len(batch)
    batch = _drop_duplicate_messages(batch)
    if not batch:
        step_logger.success(
            f"Batch processed: all {received_count} message(s) were text this pipeline has already "
            "structured before — nothing sent to GLM"
        )
        return

    step_logger.step(f"Sending batch of {len(batch)} qualified message(s) to GLM for structuring")
    properties, requirement_messages = property_structurer.structure_batch_with_routing(batch)

    # Before the property work below, and deliberately not conditional on it:
    # these messages produced no property at all (see
    # property_structurer.structure_batch_with_routing), so nothing here can
    # compete with them and there is no reason to make them wait on an
    # embedding pass they have no part in.
    _forward_to_requirement_pipeline(requirement_messages)

    _record_area_knowledge(properties)

    stored_count = 0
    needs_review_this_batch = 0
    outsider_count_this_batch = 0

    for prop in properties:
        embedded = _embed(prop)
        if embedded is None:
            continue

        if embedded.review_status == "outsider":
            outsider_count_this_batch += 1
            _outsider_count += 1

        # Set by the structuring stage, not decided here — see
        # property_structurer._apply_information_review, which is also
        # where the bar for it is enforced.
        if embedded.needs_review:
            needs_review_this_batch += 1
            _needs_review_count += 1
            step_logger.warn(
                f"Property from message {embedded.source_message_id!r} carries almost no usable "
                f"information — stored in the review queue for a human to complete: "
                f"{embedded.review_notes or 'no reason given'}"
            )

        property_vector_store.add_property(embedded)
        stored_count += 1

    step_logger.success(
        f"Batch processed: {stored_count} propert{'y' if stored_count == 1 else 'ies'} stored "
        f"({needs_review_this_batch} needing review, {outsider_count_this_batch} outsider), out of "
        f"{len(batch)} message(s) structured"
        + (f" ({received_count - len(batch)} skipped as already-seen text)" if received_count != len(batch) else "")
    )


def _forward_to_requirement_pipeline(messages: List[WhatsAppChatMessage]) -> None:
    """Hands the messages the LLM identified as DEMANDS (PART 1's
    is_requirement) over to the requirement pipeline.

    They go into the requirement BUFFER rather than straight into a
    requirement LLM call, so they ride along with whatever the keyword filter
    is already collecting and share a batch with it — one or two re-routed
    messages must not each buy their own API call. Nothing new is written to
    the database on this path: a demand becomes a requirement row (no
    embedding vector, no fingerprint lookup), which is strictly less database
    work than the property row it would otherwise have become.

    The import is local because whatsapp_service imports THIS module at
    module level — the same lazy-import pattern requirement_pipeline_service
    uses to reach the matching feature. Swallowed broadly for the same reason
    as _record_area_knowledge: this is the tail end of a batch that has
    already succeeded, and a re-route failure must never take the properties
    down with it."""
    if not messages:
        return
    try:
        from Service.WhatsAppDataFetchingService import whatsapp_service

        whatsapp_service.enqueue_requirement_messages(messages)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(
            f"Could not re-route {len(messages)} demand message(s) to the requirement pipeline "
            f"(the properties in this batch are unaffected): {exc!r}"
        )


def _drop_duplicate_messages(batch: List[WhatsAppChatMessage]) -> List[WhatsAppChatMessage]:
    """Filters out every message whose text this pipeline has already turned
    into properties, BEFORE the LLM stage runs — the client's own
    observation is that a re-posted listing arrives as the identical text,
    so this is where the duplicates in this domain actually get caught.

    Two things are checked, in this order, because neither covers the other:
      - within THIS batch, so the same text forwarded twice a minute apart
        (both copies still unstored) is structured once, not twice;
      - against what is already stored, via one indexed fingerprint lookup
        per message (Service/WhatsAppDataFetchingService/message_fingerprint.py)
        — no stored message text is ever loaded or compared.

    A message whose text has no fingerprint (blank/whitespace-only after
    normalization) is always kept: there is nothing to match it on, and
    silently dropping it would be a guess. Same for anything the lookup
    can't answer — this only ever skips a message it positively recognises."""
    global _duplicate_message_count

    kept: List[WhatsAppChatMessage] = []
    seen_in_batch: Dict[str, str] = {}

    for message in batch:
        if not message_fingerprint.is_fingerprintable(message.text):
            kept.append(message)
            continue
        text_fingerprint = message_fingerprint.fingerprint(message.text)

        earlier_in_batch = seen_in_batch.get(text_fingerprint)
        if earlier_in_batch is not None:
            _duplicate_message_count += 1
            step_logger.info(
                f"Message {message.message_id!r} is the same text as {earlier_in_batch!r}, earlier in this "
                "same batch — structuring it once instead of twice."
            )
            continue

        already_stored = property_vector_store.find_message_id_by_fingerprint(text_fingerprint)
        if already_stored is not None:
            _duplicate_message_count += 1
            step_logger.info(
                f"Message {message.message_id!r} is an exact re-post of already-processed message "
                f"{already_stored!r} — skipped before the LLM stage, so it costs nothing to structure."
            )
            continue

        seen_in_batch[text_fingerprint] = message.message_id
        kept.append(message)

    return kept


def _record_area_knowledge(properties: List[StructuredProperty]) -> None:
    """Side-channel off the structuring stage: every property the LLM just
    produced is shown to the internal area knowledge base, which files its
    place strings (area/address/society) under its area and records whether
    it already knew each one. See area_knowledge_service.

    Deliberately observational and deliberately inert. It runs BEFORE the
    embedding/duplicate/store stages purely so it sees the LLM's output
    exactly as produced, and it returns the same list untouched — the
    properties it inspects are not modified, no verdict changes, and the LLM
    prompt is not affected in any way. This is why the whole call is
    swallowed here: the knowledge base is a by-product, and a by-product must
    never be able to cost a real listing. Everything downstream runs
    identically whether this succeeds, fails, or is deleted outright."""
    try:
        area_knowledge_service.observe_properties(properties)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Area knowledge base update failed (properties are unaffected): {exc!r}")


def _embed(prop: StructuredProperty) -> Optional[EmbeddedProperty]:
    try:
        vector = embedding_service.embed_property(prop)
    except Exception as exc:  # noqa: BLE001
        # A single bad embedding must never cost the whole batch — the
        # other properties in it are still perfectly good.
        step_logger.error(f"Failed to embed property from message {prop.source_message_id!r}: {exc!r}")
        return None
    return EmbeddedProperty(
        **prop.model_dump(),
        embedding=vector,
        embedding_model=embedding_service.EMBEDDING_MODEL_NAME,
    )


def get_properties(limit: int = 100) -> List[PropertyRecord]:
    """Backs the Properties/Landing Page/Inquiries pages' polling list —
    deliberately the lightweight summary (see property_vector_store.
    get_all_properties_summary): no photo bytes, just an accurate count.
    Callers that need one property's actual photos (opening its detail or
    Edit dialog) use get_property(record_id) below instead."""
    return [_to_record(prop, image_count=count) for prop, count in property_vector_store.get_all_properties_summary(limit=limit)]


def get_property(record_id: str) -> Optional[PropertyRecord]:
    """The single-record counterpart to get_properties — one property's
    full CONTENT, served from the in-memory snapshot, with an accurate
    photo count but no photo data. Backs GET /properties/{record_id}.

    Photos are deliberately not here any more. Opening a property used to
    transfer its entire photo payload — base64, frequently megabytes —
    whether or not anyone ever looked at the photos, on every open, for
    every dialog. They now come from get_property_images below, only when
    someone actually presses Show photos."""
    found = property_vector_store.get_property_info(record_id)
    if found is None:
        return None
    prop, image_count = found
    return _to_record(prop, image_count=image_count)


def get_property_images(record_id: str) -> Optional[List[str]]:
    """One property's photos, on demand — backs
    GET /properties/{record_id}/images. None when the property doesn't
    exist, which the caller must not confuse with [] (no photos)."""
    return property_vector_store.get_property_images(record_id)


def get_property_count() -> int:
    return property_vector_store.get_property_count()


def get_properties_version() -> str:
    return property_vector_store.get_properties_version()


def get_property_version(record_id: str) -> Optional[str]:
    """Per-property change marker for HTTP caching — see
    property_vector_store.get_property_version."""
    return property_vector_store.get_property_version(record_id)


def create_property(content_fields: Dict[str, Any]) -> PropertyRecord:
    """Backs the Properties page's Add dialog — a property entered by hand
    rather than extracted from a WhatsApp message. Every WhatsApp-metadata
    field StructuredProperty otherwise requires (sender, group, message
    text/timestamp) gets a placeholder here instead, since none of it
    exists for a manual entry; every content field is optional, matching
    the dialog itself (see property_controller.py's PropertyContentFields).

    Stored directly as review_status="accepted", needs_review=False — a
    human deliberately adding a property already knows what is in it, so
    second-guessing how complete it is would be pointless. The review queue
    is only ever populated by the LLM structuring stage."""
    fields = {key: value for key, value in content_fields.items() if key in EDITABLE_CONTENT_FIELDS}
    now = datetime.now(timezone.utc)
    structured = StructuredProperty(
        source_message_id=f"manual-{uuid.uuid4().hex}",
        group_name="Manually added",
        chat_type="personal",
        sender_name="Manual entry",
        sender_saved_name="Manual entry",
        sender_phone="manual",
        message_text=fields.get("description") or "Added manually via the Properties page.",
        message_timestamp=now,
        **fields,
    )
    # A manual Add that already includes a photo or reel link counts as
    # qualifying for the Landing Page page from the moment it's created —
    # same signal update_property computes below for an edit, just inlined
    # here since create_property builds the StructuredProperty directly
    # rather than going through content_updates.
    if structured.image_urls or structured.instagram_reel_url:
        structured.qualified_at = now
    embedded = _embed(structured)
    if embedded is None:
        # Embedding only fails on an unexpected model error (see _embed) —
        # exceedingly rare for a hand-typed property, but a manual Add must
        # never silently do nothing, so this is a real error, not a no-op.
        raise RuntimeError("Could not save this property — the embedding step failed. Please try again.")
    property_vector_store.add_property(embedded)
    return _to_record(embedded)


def update_property(
    record_id: str,
    review_status: Optional[str] = None,
    needs_review: Optional[bool] = None,
    content_updates: Optional[Dict[str, Any]] = None,
    landing_page: Optional[bool] = None,
) -> Optional[PropertyRecord]:
    """Backs four actions the UI offers on a stored property: "move to
    Main/Outsider" (review_status), "accept out of the review queue"
    (needs_review=False), the Properties page's Edit dialog
    (content_updates), and the Landing Page page's Send/Remove actions
    (landing_page) — any of the four can be passed alone, or together.
    Returns None if no property with this record_id exists.

    content_updates triggers a full embedding recompute, over the property's
    OTHER fields merged with the edit — never a partial/stale vector — so a
    hand-edited property stays exactly as comparable for client-property
    match scoring as one the LLM structured, with no second, out-of-date
    vector left behind from before the edit. This is what makes completing a
    Needs review property by hand actually put it in front of matching
    clients.

    An edit that adds a photo or an Instagram reel link also bumps
    qualified_at — the Landing Page page's Ready to Add tab sorts newly
    qualified properties to the top by this, and Send/Remove deliberately
    never touch it (see StructuredProperty.qualified_at)."""
    embedding_kwargs: Dict[str, Any] = {}
    filtered_updates: Optional[Dict[str, Any]] = None
    qualified_at: Optional[datetime] = None
    if content_updates:
        filtered_updates = {key: value for key, value in content_updates.items() if key in EDITABLE_CONTENT_FIELDS}
        # The photo-less snapshot copy, not the full row: this merge exists
        # only to recompute the embedding, and embedding text is built from
        # the property's words (see embedding_service.build_embedding_text)
        # — image_urls has never been part of it. Reading the full row here
        # meant every single edit, including one that only changed a price,
        # pulled that property's entire photo payload out of the database to
        # throw it away moments later.
        found = property_vector_store.get_property_info(record_id)
        if found is None:
            return None
        existing = found[0]
        merged_data = existing.model_dump(exclude={"embedding", "embedding_model"})
        merged_data.update(filtered_updates)
        merged_structured = StructuredProperty(**merged_data)
        embedding_kwargs = {
            "embedding": embedding_service.embed_property(merged_structured),
            "embedding_model": embedding_service.EMBEDDING_MODEL_NAME,
        }
        if filtered_updates.get("image_urls") or filtered_updates.get("instagram_reel_url"):
            qualified_at = datetime.now(timezone.utc)

    updated = property_vector_store.update_property(
        record_id,
        review_status=review_status,
        needs_review=needs_review,
        content_updates=filtered_updates,
        on_landing_page=landing_page,
        qualified_at=qualified_at,
        **embedding_kwargs,
    )
    return _to_record(updated) if updated is not None else None


def delete_property(record_id: str) -> bool:
    return property_vector_store.delete_property(record_id)


def _to_record(prop: EmbeddedProperty, image_count: Optional[int] = None) -> PropertyRecord:
    use_24_hour_format = display_settings_service.get_use_24_hour_format()
    return PropertyRecord(
        **prop.model_dump(exclude=_NON_API_FIELDS),
        formatted_timestamp=timestamp_formatting.format_ist(prop.message_timestamp, use_24_hour_format),
        # Passed explicitly by get_properties, whose summary rows have
        # image_urls=[] (see property_vector_store.get_all_properties_summary)
        # — falling back to len(prop.image_urls) here would silently report
        # zero photos for every property on that path. Every other caller
        # (create/update/get_property) loads full rows, so the fallback is
        # exact for them.
        image_count=image_count if image_count is not None else len(prop.image_urls),
    )


def get_duplicate_message_count() -> int:
    """Messages skipped by _drop_duplicate_messages since this process
    started — i.e. re-posts that cost no LLM call at all."""
    return _duplicate_message_count


def get_needs_review_count() -> int:
    """Properties this process has stored into the review queue for
    carrying almost no usable information."""
    return _needs_review_count


def get_outsider_count() -> int:
    return _outsider_count
