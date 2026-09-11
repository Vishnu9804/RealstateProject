"""The "mark this property sold out" action, end to end.

WHAT SOLD OUT MEANS HERE

Once a deal is done the listing is not a listing any more, so it does not
merely get a flag — it LEAVES the property database and lands in one place
of its own (Database/soldout_property_models.py's `soldout_properties`,
surfaced as the Properties page's Sold out tab). Because the row is gone
from `properties`, every existing read in the application stops returning
it without knowing this feature exists:

  - the Properties page's Main/Outsider/Needs review tabs, and the Landing
    Page page, both read the property list;
  - the public landing site reads only published `properties` rows, so a
    published listing disappears from the website too;
  - client-property match scoring, the daily rescore, the broker-requirement
    matcher and the manual property picker all read that same list;
  - the Instagram poller's watched reels are derived from it as well.

What does NOT clean itself up that way is anything that stored the
property's id somewhere else — a client's cached match scores, an
operator's hand-picked shortlist, and an agent's pending site visits. Those
are removed here, inside the same transaction as the move itself, so the
property's removal and the disappearance of every reference to it can never
come apart. See Database/soldout_property_repository.py.

THE AGENTS ARE TOLD ONCE

An agent who was sent this property for four different clients has four
assignment rows but is one person: they get ONE message saying the property
is sold and every visit to it is cancelled, not four. Sending is
best-effort and happens after the move has committed — exactly the same
rule the existing cancellation path follows (see
Controller/WhatsAppInquiryHandlingController/whatsapp_inquiry_controller.py's
_cancel_active_assignments): the visits are already cancelled, and a
WhatsApp that fails to reach one agent must not leave the other agents
un-messaged or the cancellation half-applied.

COMPLETED VISITS ARE KEPT

A visit that actually took place is permanent history, and the agents' own
visit counts are built from it (Database/agent_visit_models.py). Cancelling
a pending visit and un-recording one already made are different things, and
only the first is what selling a property means.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from Database import soldout_property_repository
from Database.session import is_database_configured
from Middleware import step_logger
from Model.WhatsAppDataFetchingModel.soldout_property import SoldOutPropertyRecord
from Service.WhatsAppDataFetchingService import (
    display_settings_service,
    property_snapshot,
    property_vector_store,
    soldout_property_store,
    timestamp_formatting,
)

# What the property's own record carries that a sold-out row deliberately
# does not (see Database/soldout_property_models.py) — dropped when the
# in-memory fallback builds its sold-out copy from a live property.
_DROPPED_PROPERTY_FIELDS = {
    "embedding",
    "embedding_model",
    "on_landing_page",
    "landing_page_updated_at",
    "qualified_at",
}


@dataclass
class SoldOutResult:
    """What marking one property sold out actually did — reported straight
    back to the operator who pressed the button, so a WhatsApp that failed
    to send is visible rather than silently reported as done (same shape of
    honesty as the existing CancelResult)."""

    property: SoldOutPropertyRecord
    visits_cancelled: int
    agents_notified: int
    agents_failed: int


def mark_sold_out(record_id: str) -> Optional[SoldOutResult]:
    """Moves one property into the sold-out table, cancels every pending
    site visit to it, and tells each agent involved once. Returns None when
    no property with this record_id exists (the caller turns that into a
    404) — and in that case nothing at all has been written."""
    if is_database_configured():
        outcome = soldout_property_repository.move_property_to_soldout(record_id)
        if not outcome.found or outcome.row is None:
            return None
        # The in-memory property snapshot is the list every page and every
        # scoring pass reads (see property_snapshot's own docstring), so the
        # property is only really gone once it is dropped from there too.
        # Done AFTER the transaction committed, never before.
        property_snapshot.note_removed(outcome.row.fields["record_id"])
        soldout_property_store.note_added(outcome.row.fields, outcome.row.image_count)
        notified, failed = _notify_agents(
            [(agent.name, agent.phone) for agent in outcome.agents],
            outcome.property_label,
        )
        record = _to_record(outcome.row.fields, outcome.row.image_count)
        step_logger.success(
            f"Property {record.record_id!r} marked sold out — moved out of the property database, "
            f"{outcome.visits_cancelled} pending site visit(s) cancelled, {notified} agent(s) notified."
        )
        return SoldOutResult(
            property=record,
            visits_cancelled=outcome.visits_cancelled,
            agents_notified=notified,
            agents_failed=failed,
        )

    return _mark_sold_out_in_memory(record_id)


def _mark_sold_out_in_memory(record_id: str) -> Optional[SoldOutResult]:
    """The no-DATABASE_URL path. Same sequence as the database one, against
    the in-memory stores — see each store's own "in-memory fallback only"
    helper for why the cleanup lives there rather than being duplicated
    here."""
    # Lazy imports, deliberately: these are the only two places this module
    # touches the client-records and agent features at all, and neither
    # should hard-depend on the other at module load (the same reasoning
    # agent_store.record_assignment documents for its own lead_store import).
    from Service.AgentManagementService import agent_store, manual_property_store
    from Service.ClientPropertyMatchingService import matching_service

    prop = property_vector_store.get_property(record_id)
    if prop is None:
        return None

    removed_assignments = agent_store.take_memory_assignments_for_property(record_id)
    matching_service.drop_property_from_memory_cache(record_id)
    manual_property_store.drop_property_from_memory(record_id)

    fields = prop.model_dump(exclude=_DROPPED_PROPERTY_FIELDS)
    fields["sold_out_at"] = _now()
    fields["created_at"] = None
    soldout_property_store.add_in_memory(fields)
    property_vector_store.delete_property(record_id)

    agents: List[tuple] = []
    seen_agent_ids = set()
    label: Optional[str] = None
    for assignment in removed_assignments:
        label = label or assignment.property_label
        if assignment.agent_id in seen_agent_ids:
            continue
        seen_agent_ids.add(assignment.agent_id)
        agent = agent_store.get_agent_by_id(assignment.agent_id)
        if agent is not None:
            agents.append((agent.name, agent.phone))
    label = label or " · ".join(part for part in (prop.society_name, prop.area_name) if part) or None

    notified, failed = _notify_agents(agents, label)
    return SoldOutResult(
        property=_to_record(fields, len(prop.image_urls)),
        visits_cancelled=len(removed_assignments),
        agents_notified=notified,
        agents_failed=failed,
    )


def _notify_agents(agents: List[tuple], property_label: Optional[str]) -> tuple:
    """One WhatsApp per agent, never one per cancelled visit — see this
    module's docstring. Returns (notified, failed), counting agents actually
    REACHED rather than messages attempted, so a send that didn't go out is
    reported instead of being rounded up into a success."""
    if not agents:
        return 0, 0

    # Lazy imports for the same reason as above, and because this is the
    # only thing in the property-fetching feature that sends a message at
    # all.
    from Service.WhatsAppInquiryHandlingService import outbound_messenger
    from Service.WhatsAppInquiryHandlingService.phone_utils import normalize_phone

    what = property_label or "A property you were handling"
    message = (
        "🚫 Property sold out\n\n"
        f"{what}\n\n"
        "This property is sold out, so every site visit you had pending for it is cancelled — "
        "please don't take any client there. If anything changes, we'll let you know."
    )

    notified = 0
    failed = 0
    for name, phone in agents:
        target = normalize_phone(phone) or phone
        if outbound_messenger.send_text(target, message):
            notified += 1
        else:
            failed += 1
            step_logger.warn(
                f"Could not tell agent {name!r} that a property they were handling is sold out — "
                "their pending visits are already cancelled; tell them by hand."
            )
    return notified, failed


def get_sold_out_properties(limit: int = 500) -> List[SoldOutPropertyRecord]:
    """The Sold out tab's list, served entirely from memory — see
    soldout_property_store's own docstring. Photo-less by construction
    (`image_urls` is always [], `image_count` carries the real number),
    exactly like the properties list endpoint."""
    return [_to_record(entry.fields, entry.image_count) for entry in soldout_property_store.get_all(limit)]


def get_sold_out_property_images(record_id: str) -> Optional[List[str]]:
    """One sold-out property's photos, on demand. None when it doesn't
    exist, which the caller must not confuse with [] (no photos)."""
    return soldout_property_store.get_images(record_id)


def get_sold_out_version() -> str:
    return soldout_property_store.version()


def get_sold_out_count() -> int:
    return soldout_property_store.count()


def get_sold_out_ids() -> set:
    """The in-memory set of sold-out property ids — see
    soldout_property_store.get_sold_out_ids for who reads it and why."""
    return soldout_property_store.get_sold_out_ids()


def _to_record(fields: dict, image_count: int) -> SoldOutPropertyRecord:
    """Builds the API shape from stored column values.

    `image_urls` is forced to [] regardless of what `fields` holds: the
    database path never loads that column (it is deferred), and the
    in-memory fallback does hold it — sending it here either way would put
    megabytes of base64 into a list response. The real number travels as
    `image_count`, and the photos come from the images endpoint. This is the
    same contract the properties list endpoint already has.
    """
    use_24_hour_format = display_settings_service.get_use_24_hour_format()
    data = {key: value for key, value in fields.items() if key not in ("image_urls", "created_at")}
    return SoldOutPropertyRecord(
        **data,
        image_urls=[],
        image_count=image_count,
        formatted_timestamp=timestamp_formatting.format_ist(fields["message_timestamp"], use_24_hour_format),
        formatted_sold_out_at=timestamp_formatting.format_ist(fields["sold_out_at"], use_24_hour_format),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)
