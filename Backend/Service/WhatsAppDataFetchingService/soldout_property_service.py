"""The "this deal is done" action, and everything that has to stop being
true the moment it fires.

A sold property is not a property with a flag on it. Once the deal closes
there is nothing left to match a client against, publish on the website,
hand to an agent, or edit — so the listing LEAVES the `properties` table
altogether and lands in `soldout_properties`, where the Sold out view is
the only thing that reads it. Every other feature in this project already
reads the live property table (matching, the landing page, the Instagram
reel poller, the hand-pick and select-property screens), so a property that
is no longer in it is, by construction, gone from all of them at once —
there is no list anywhere that has to remember to exclude it.

What is NOT automatic, and is therefore done explicitly here, is the state
OTHER tables hold *about* a property:

  - Active site visits (agent_assignments). These are cancelled, and each
    agent holding one is told on WhatsApp that the property has sold and
    their visit is off — the same courtesy, over the same connection, as
    the existing "clear this client's assignments" action.
  - Cached match scores (client_property_matches). The matches dialog
    already skips a score whose property has gone, but the per-bucket
    COUNTS on the Inquiries table are read straight off that cache, so the
    rows are dropped rather than left to inflate a badge.
  - Hand-picked properties (client_manual_properties). A hand-pick is a
    promise to show someone this exact listing; there is nothing to show.

COMPLETED visits (agent_visits) are deliberately kept. A visit that already
happened still happened, the agent still did the work, and that history is
what stops the same client being offered the same property twice. "Sold"
describes the listing's future, not its past.

The move itself is one database transaction (see
soldout_property_store.move_property_to_soldout): a property is never in
both tables, and never in neither. The clean-up and the WhatsApp messages
run after it, each guarded on its own, because a property that has sold has
sold — a failure to reach one agent must never leave the sale un-recorded
or the property half-removed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Set

from Middleware import step_logger
from Model.WhatsAppDataFetchingModel.soldout_property import (
    SoldOutMoveResult,
    SoldOutProperty,
    SoldOutPropertyRecord,
)
from Service.WhatsAppDataFetchingService import (
    display_settings_service,
    property_vector_store,
    soldout_property_store,
    timestamp_formatting,
)


def mark_property_sold_out(record_id: str) -> Optional[SoldOutMoveResult]:
    """Moves one live property into the sold-out table and unwinds
    everything that pointed at it. Returns None if no such property exists
    (already sold out, or never existed) — the caller turns that into a 404.
    """
    prop = property_vector_store.get_property(record_id)
    if prop is None:
        return None

    sold_out = SoldOutProperty(
        # exclude the vector: a sold property is never scored again, and
        # SoldOutProperty has no field to put it in anyway.
        **prop.model_dump(exclude={"embedding", "embedding_model"}),
        sold_out_at=datetime.now(timezone.utc),
    )

    # Read the active visits BEFORE anything is deleted — this is the only
    # moment the agent/client details behind them are still available to
    # message from.
    assignments = _active_assignments_for(record_id)

    # The move. Anything raising here propagates: the property is untouched
    # and the operator gets a real error instead of a silent half-action.
    moved = soldout_property_store.move_property_to_soldout(sold_out)
    if not moved:
        step_logger.info(
            f"Property {record_id!r} was already recorded as sold out — the live row has been removed "
            "again just in case, and nothing else was changed."
        )

    cancelled = _cancel_assignments(record_id)
    cleared_matches = _clear_cached_matches(record_id)
    cleared_manual = _clear_manual_picks(record_id)
    notified, failed = _notify_agents(assignments, sold_out)

    step_logger.success(
        f"Property {record_id!r} moved to Sold out — {cancelled} active site visit(s) cancelled "
        f"({notified} agent(s) notified, {failed} unreachable), {cleared_matches} cached match(es) and "
        f"{cleared_manual} hand-pick(s) removed."
    )

    return SoldOutMoveResult(
        # image_count, not the photos themselves: a property with a dozen
        # base64 photos would otherwise put several megabytes into the
        # response to a button press, for a record the frontend only shows in
        # its (photo-less) list. Matches what GET /soldout-properties returns
        # for the same row, so the two can't disagree.
        property=_to_record(sold_out.model_copy(update={"image_urls": []}), image_count=len(sold_out.image_urls)),
        cancelled_visits=cancelled,
        agents_notified=notified,
        agents_failed=failed,
        cleared_matches=cleared_matches,
        cleared_manual_picks=cleared_manual,
    )


# --------------------------------------------------------------------------
# read side — what the Sold out view shows
# --------------------------------------------------------------------------


def get_soldout_properties(limit: int = 500) -> List[SoldOutPropertyRecord]:
    """Newest sale first, and deliberately photo-less — same summary trick
    the Properties list uses (see property_pipeline_service.get_properties);
    `image_count` is accurate, `image_urls` is empty. Callers needing one
    property's actual photos use get_soldout_property below."""
    return [
        _to_record(prop, image_count=count) for prop, count in soldout_property_store.get_all_summary(limit=limit)
    ]


def get_soldout_property(record_id: str) -> Optional[SoldOutPropertyRecord]:
    """The single-record counterpart to get_soldout_properties — full
    content, photos included."""
    prop = soldout_property_store.get(record_id)
    return _to_record(prop) if prop is not None else None


def get_soldout_count() -> int:
    return soldout_property_store.get_count()


def delete_soldout_property(record_id: str) -> bool:
    """Erases one sold-out record permanently. The Sold out view's only
    write action — there is no way back to the live table on purpose: a
    closed deal is closed, and a property re-listed later is a new listing
    with its own message and its own record."""
    return soldout_property_store.delete(record_id)


def filter_soldout_record_ids(record_ids: Sequence[str]) -> Set[str]:
    """Which of these property ids are sold out. Exposed here so callers
    outside this feature (see Service/LandingPageService/landing_page_service.py)
    ask the service rather than reaching into the store."""
    return soldout_property_store.filter_soldout_record_ids(record_ids)


# --------------------------------------------------------------------------
# the unwinding — each step guarded on its own (see the module docstring)
# --------------------------------------------------------------------------


def _active_assignments_for(record_id: str) -> List:
    """Every agent currently holding a site visit for this property.

    Lazily imported, and never allowed to raise: this is the courtesy read
    that feeds the notification below, and the AgentManagement feature being
    momentarily unavailable must not be able to stop a property from being
    recorded as sold. A lazy import also keeps this module's load-time graph
    free of the agent/inquiry features entirely — the same convention
    client_store.upsert_client and agent_store.record_assignment already use
    for their own cross-feature calls."""
    try:
        from Service.AgentManagementService import agent_store

        return agent_store.get_active_assignments_for_property(record_id)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Could not read the active site visits for property {record_id!r}: {exc!r}")
        return []


def _cancel_assignments(record_id: str) -> int:
    try:
        from Service.AgentManagementService import agent_store

        return len(agent_store.clear_assignments_for_property(record_id))
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Could not cancel the active site visits for sold property {record_id!r}: {exc!r}")
        return 0


def _clear_cached_matches(record_id: str) -> int:
    try:
        from Service.ClientPropertyMatchingService import matching_service

        return matching_service.remove_property_from_matches(record_id)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Could not clear cached matches for sold property {record_id!r}: {exc!r}")
        return 0


def _clear_manual_picks(record_id: str) -> int:
    try:
        from Service.AgentManagementService import manual_property_store

        return manual_property_store.remove_property_for_all_clients(record_id)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Could not clear hand-picked entries for sold property {record_id!r}: {exc!r}")
        return 0


def _notify_agents(assignments: List, prop: SoldOutProperty) -> tuple[int, int]:
    """One WhatsApp per AGENT, not per assignment.

    An agent holding this same property for two different clients is two
    rows but one person, and telling them twice about the same sale reads
    like a bug — so the clients they were handling it for are named inside
    the single message instead (they need to know which visits to drop).

    Best-effort by design, exactly like the existing hand-off cancellation:
    the visits are already cancelled by the time these go out, and a message
    that fails to reach one agent must not leave the others un-messaged.
    """
    if not assignments:
        return 0, 0

    try:
        from Service.AgentManagementService import agent_store
        from Service.WhatsAppInquiryHandlingService import outbound_messenger
        from Service.WhatsAppInquiryHandlingService.phone_utils import normalize_phone
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Could not load the WhatsApp sender to notify agents about a sold property: {exc!r}")
        return 0, len({a.agent_id for a in assignments})

    label = _property_label(prop)
    clients_by_agent: Dict[str, List[str]] = {}
    phone_by_agent: Dict[str, str] = {}
    for assignment in assignments:
        who = f"{assignment.client_name or 'this client'} ({assignment.client_phone})"
        entries = clients_by_agent.setdefault(assignment.agent_id, [])
        if who not in entries:
            entries.append(who)
        if assignment.agent_id not in phone_by_agent:
            agent = agent_store.get_agent_by_id(assignment.agent_id)
            # Fall back to nothing rather than guessing: an agent deleted
            # since the assignment was made has no number to reach.
            if agent is not None:
                phone_by_agent[assignment.agent_id] = agent.phone

    notified = 0
    failed = 0
    for agent_id, clients in clients_by_agent.items():
        agent_phone = phone_by_agent.get(agent_id)
        if not agent_phone:
            failed += 1
            continue
        message = (
            f"Update: {label} is already sold out, so your site visit to this property is cancelled.\n\n"
            f"Client: {', '.join(clients)}\n\n"
            "Please do not proceed with this visit. If any updates, they will be provided."
        )
        target = normalize_phone(agent_phone) or agent_phone
        if outbound_messenger.send_text(target, message):
            notified += 1
        else:
            failed += 1
    return notified, failed


def _property_label(prop: SoldOutProperty) -> str:
    """A readable name for the listing in an agent-facing message. Every
    part is optional in the data, so this degrades one step at a time
    instead of rendering "None in None"."""
    head = " ".join(part for part in (prop.bhk, prop.property_type) if part).strip()
    place = prop.society_name or prop.area_name
    if head and place:
        return f"{head} in {place}"
    return head or place or "This property"


def _to_record(prop: SoldOutProperty, image_count: Optional[int] = None) -> SoldOutPropertyRecord:
    use_24_hour_format = display_settings_service.get_use_24_hour_format()
    return SoldOutPropertyRecord(
        **prop.model_dump(),
        formatted_timestamp=timestamp_formatting.format_ist(prop.message_timestamp, use_24_hour_format),
        formatted_sold_out_at=timestamp_formatting.format_ist(prop.sold_out_at, use_24_hour_format),
        # Passed explicitly by get_soldout_properties, whose summary rows
        # carry image_urls=[] — falling back to len() there would report
        # zero photos for every sold property. Every other caller loads full
        # rows, so the fallback is exact for them.
        image_count=image_count if image_count is not None else len(prop.image_urls),
    )
