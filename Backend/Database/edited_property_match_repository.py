"""Stored matches that an EDITED listing invalidates — removed here, in one
transaction, the moment the edit is saved.

WHY THIS EXISTS

A cached match says "this property scored 84% against that client/requirement"
— a statement about the property AS IT WAS when it was scored. Edit the
property's price, BHK, area or wording and that statement may simply no longer
be true, yet it kept sitting in the client's matches (until the 6 AM pass) and
in the requirement's stored rows (until someone opened its dialog), where it
could be shared with a broker or handed to an agent in the meantime.

So an edit drops those rows outright. Nothing is re-scored here: the listing's
updated_at has just moved, so the machinery that already exists picks it up and
scores it AGAIN, against its new content — a requirement's matches in the
6 AM pass (requirement_matching_service.rescore_all_requirements) or
immediately on that requirement's dialog's own "Refresh", and a client's
matches in the nightly incremental pass (scheduled_recompute_service) or
immediately on "Refresh matches". Neither dialog re-scores merely by being
opened — both are cache-only reads (matching_service.get_cached_result,
requirement_matching_service.get_matches_for_requirement).
If it still matches, it comes back with a correct score; if it does not, it
never should have been there. That is why removing is safe AND cheap:
re-scoring at edit time would mean reading every client's stored requirement
vector out of the database, which is exactly the traffic this project avoids.

THE ONE EXCEPTION: ASSIGNED AND COMPLETED

A property a client has already been sent to see — an agent is assigned to it,
or a visit to it has been completed — is no longer merely a "match". It is part
of that client's history, and it must not vanish from their matches dialog
because someone corrected a typo in the listing afterwards. Those rows are kept
(their card then shows the property's CURRENT content, because every match
result is enriched from the live property list at read time — see
matching_service._build_result), and every other client's row for that property
goes.

Broker requirements have no such concept — a requirement's matches are simply
the shortlist we send over — so there the removal is unconditional.

WHY ONE FUNCTION AND ONE TRANSACTION

Same reasoning as Database/soldout_property_repository.py, which does the same
kind of cross-feature clean-up: both match tables live on the one engine every
table here shares (Database/client_session.get_client_session IS get_session),
so this is one connection checkout and one BEGIN/COMMIT against a database
billed by compute time, not two.

WHY IT MOVES NO DATA

Both statements are DELETEs with the protection expressed as correlated NOT
EXISTS subqueries, so Postgres decides which rows survive without sending a
single row — or a single client phone number — to this process. The only thing
that comes back is how many rows each statement removed.
"""

from __future__ import annotations

from typing import Tuple

from sqlalchemy import delete, select

from Database.agent_assignment_models import AgentAssignmentRow
from Database.agent_visit_models import AgentVisitRow
from Database.broker_requirement_match_models import BrokerRequirementMatchRow
from Database.client_property_match_models import ClientPropertyMatchRow
from Database.session import get_session


def purge_matches_for_edited_property(record_id: str) -> Tuple[int, int]:
    """Drops this listing's now-stale match rows and returns
    (client rows removed, requirement rows removed).

    `record_id` is the same string both match tables store (a property's
    StructuredProperty.record_id, or a builder project's — they share one id
    space as match candidates, see Service/ClientPropertyMatchingService/
    match_candidates.py), which is why one call covers both kinds of listing.
    """
    # "This client already has an agent out to this property" and "this client
    # has already completed a visit to it". Correlated on client_phone, so each
    # one asks the question per surviving row rather than for the table.
    assigned = (
        select(AgentAssignmentRow.id)
        .where(
            AgentAssignmentRow.property_record_id == record_id,
            AgentAssignmentRow.client_phone == ClientPropertyMatchRow.client_phone,
        )
        .exists()
    )
    completed = (
        select(AgentVisitRow.visit_id)
        .where(
            AgentVisitRow.property_record_id == record_id,
            AgentVisitRow.client_phone == ClientPropertyMatchRow.client_phone,
        )
        .exists()
    )
    with get_session() as session:
        client_rows = session.execute(
            delete(ClientPropertyMatchRow).where(
                ClientPropertyMatchRow.property_record_id == record_id,
                ~assigned,
                ~completed,
            )
        ).rowcount
        requirement_rows = session.execute(
            delete(BrokerRequirementMatchRow).where(
                BrokerRequirementMatchRow.property_record_id == record_id
            )
        ).rowcount
    # rowcount is exact for a plain DELETE (unlike the INSERT ... SELECT in
    # soldout_property_repository, where the driver reports -1), but a driver
    # that cannot tell still returns -1 — reported as 0 rather than as a
    # negative count in a log line.
    return max(client_rows, 0), max(requirement_rows, 0)
