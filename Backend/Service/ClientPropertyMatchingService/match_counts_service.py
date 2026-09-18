"""The Inquiries table's Matches / Completed / Status numbers, for every
client at once.

WHAT THIS REPLACES

Those three columns are driven by one MatchCounts per client, and until now
the only way to get one was GET /matching/clients/{phone}/counts — a
per-client endpoint. A table of five hundred clients therefore opened five
hundred HTTP requests, and each of those ran six queries of its own: the
client's whole cached match set (twice — once for the bucket counts, once
for the ids), its hand-picked properties, its active assignments, its
completed visits and its website enquiries. With three hundred thousand
cached match rows in the table, that is several hundred thousand rows of
score data — field_scores JSON and reason text included — moved across the
wire every time the page decided its counts were stale, to end up with
about three integers per row. The browser's own six-connections-per-origin
limit then serialised the whole thing into eighty-odd round trips deep,
which is what the spinners in those columns actually were.

WHAT IT DOES INSTEAD

One request, and a build that is proportional to the SMALL lists rather
than to the match table:

  1. one aggregate for every client's per-bucket match counts
     (matching_repository.get_bucket_counts_by_client);
  2. one read each for the hand-picked, assigned, completed and
     website-enquired property ids across all clients — four small tables;
  3. one indexed probe asking which of THOSE (few hundred) properties are
     also scored, which is the only thing the aggregate in step 1 cannot
     answer on its own (matching_repository.get_scored_pairs).

Six queries in total, for any number of clients.

WHY THE NUMBERS ARE IDENTICAL TO THE PER-CLIENT ENDPOINT'S

They are not merely equivalent — both paths call the same function,
`compute` below. The per-client endpoint hands it a full scored-id set; the
bulk path hands it the scored TOTAL plus a membership test over the small
lists. Those are exactly the two things the arithmetic needs, and nothing
else about a match row enters it. There is no second copy of the rule to
drift.

CACHING

The built map is held in memory and re-used. It is rebuilt when a caller
asks for a fresh one (an operator action that just changed a count), and
otherwise at most once per _TTL_SECONDS, and only while somebody actually
has the Inquiries page open — nothing here runs on a timer. The version
string is a hash of the map itself, so a rebuild that finds the same
numbers keeps the same version and the request ends as a bodyless 304.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Callable, Dict, Optional, Set, Tuple

from Model.ClientPropertyMatchingModel.match_counts import MatchCounts
from Service.AgentManagementService import agent_store, manual_property_store
from Service.ClientPropertyMatchingService import matching_service
from Service.LandingPageService import landing_page_service
from Service.WhatsAppDataFetchingService import soldout_property_service
from Service.WhatsAppInquiryHandlingService import client_store

# How long a built map is served before the next request rebuilds it. This
# is a BACKSTOP, not the refresh mechanism: the page asks for a fresh build
# the moment an operator action changes a count, and otherwise re-reads on
# the same version signals it always used. It exists so that a change made
# somewhere this page cannot observe (the nightly rescore, another operator,
# an agent completing a visit) can never leave a stale number on screen for
# longer than this.
_TTL_SECONDS = 45.0

# How many clients are counted at once. The client list the Inquiries page
# itself reads is bounded the same way (see lib/fetchLimits.ts's
# CLIENT_FETCH_LIMIT), so the map always covers every row that table shows.
_CLIENT_LIMIT = 5000

_lock = threading.Lock()
_cached: Optional[Dict[str, MatchCounts]] = None
_cached_version: str = ""
_built_at: float = 0.0


def compute(
    bucket_counts: Dict[str, int],
    scored_total: int,
    is_scored: Callable[[str], bool],
    manual_ids: Set[str],
    website_ids: Set[str],
    assigned_ids: Set[str],
    completed_ids: Set[str],
) -> MatchCounts:
    """ONE client's counts — the single definition of what each of these
    numbers means, shared by the per-client endpoint and the bulk build.

    `scored_total` and `is_scored` together stand in for the client's scored
    id set: the caller that already holds that set passes its size and its
    membership test, and the caller that does not (the bulk path, which
    deliberately never loads three hundred thousand ids) passes the
    aggregate count and an indexed membership probe. The arithmetic below
    never needs anything else about a scored row.

    The sets are read as sets on purpose — they genuinely overlap (a
    hand-picked property can also score, a website enquiry can be either),
    and `completed` is not necessarily a subset of any of them. See
    MatchCounts.total.
    """
    picked = manual_ids | website_ids
    # (scored union manual union website): everything scored, plus whatever
    # the small lists add that was not scored already.
    union_extra = {record_id for record_id in picked if not is_scored(record_id)}
    union_size = scored_total + len(union_extra)

    def in_union(record_id: str) -> bool:
        return record_id in picked or is_scored(record_id)

    # Only completed visits whose property is still in that union — a visit
    # to a property that has since left every list must not be subtracted
    # from a total it is not part of.
    completed_in_union = {record_id for record_id in completed_ids if in_union(record_id)}
    outstanding_size = union_size - len(completed_in_union)
    # Likewise: an assignment left over from a property that has dropped out
    # must never make the Status column read "3 assigned" out of 2.
    assigned_outstanding = {
        record_id
        for record_id in assigned_ids
        if in_union(record_id) and record_id not in completed_ids
    }
    # Mirrors ClientMatchesDialog's own "Web Site Property Inquiry" section
    # exactly: never scored, never hand-picked, not already completed.
    website_only = {
        record_id
        for record_id in website_ids
        if not is_scored(record_id) and record_id not in manual_ids and record_id not in completed_ids
    }
    return MatchCounts(
        high=bucket_counts.get("high", 0),
        medium=bucket_counts.get("medium", 0),
        low=bucket_counts.get("low", 0),
        manual=len(manual_ids),
        website_only=len(website_only),
        total=outstanding_size,
        assigned=len(assigned_outstanding),
        completed=len(completed_ids),
    )


def get_all(force: bool = False) -> Tuple[Dict[str, MatchCounts], str]:
    """(phone -> counts, version) for every client currently in the list.

    `force` skips the cache — what an operator action that just changed a
    count passes, so the number on screen catches up immediately rather than
    at the end of the backstop window.
    """
    global _cached, _cached_version, _built_at
    with _lock:
        fresh_enough = _cached is not None and not force and (time.monotonic() - _built_at) < _TTL_SECONDS
        if fresh_enough:
            return _cached, _cached_version  # type: ignore[return-value]
        counts = _build()
        version = _version_of(counts)
        _cached = counts
        _cached_version = version
        _built_at = time.monotonic()
        return counts, version


def invalidate() -> None:
    """Drops the held map so the next read rebuilds. Nothing depends on this
    being called — the backstop window above already bounds staleness — so a
    write path that forgets it costs a slightly later number, never a wrong
    one that persists."""
    global _cached, _built_at
    with _lock:
        _cached = None
        _built_at = 0.0


def _build() -> Dict[str, MatchCounts]:
    # Keys only: this build counts FOR each client and never reads a
    # single field off one, so it must not pull whole client rows to
    # find out who they are.
    phones = client_store.get_all_client_phones(limit=_CLIENT_LIMIT)
    if not phones:
        return {}

    bucket_counts = matching_service.get_bucket_counts_by_client()
    manual_by_phone = manual_property_store.get_manual_properties_by_client(phones)
    assigned_by_phone = agent_store.get_assigned_property_ids_by_client(phones)
    completed_by_phone = agent_store.get_completed_property_ids_by_client(phones)
    website_by_phone = landing_page_service.get_property_ids_for_phones(phones)
    # A sold-out property is gone from every other list by the sale itself
    # (see Database/soldout_property_repository.move_property_to_soldout),
    # but a website enquiry is a record of a PERSON's interest and is
    # deliberately kept — so this is the one source that still has to be
    # filtered. An in-memory set, no query.
    sold_out = soldout_property_service.get_sold_out_ids()

    # Everything the aggregate above cannot answer, asked once for all
    # clients at once — see matching_repository.get_scored_pairs.
    pairs: Set[Tuple[str, str]] = set()
    for phone in phones:
        for source in (
            manual_by_phone.get(phone),
            website_by_phone.get(phone),
            assigned_by_phone.get(phone),
            completed_by_phone.get(phone),
        ):
            if not source:
                continue
            for record_id in source:
                if record_id:
                    pairs.add((phone, record_id))
    scored_pairs = matching_service.get_scored_pairs(pairs)

    result: Dict[str, MatchCounts] = {}
    for phone in phones:
        buckets = bucket_counts.get(phone, {})
        manual_ids = set(manual_by_phone.get(phone) or ())
        website_ids = set(website_by_phone.get(phone) or ()) - sold_out
        assigned_ids = set(assigned_by_phone.get(phone) or ())
        completed_ids = set(completed_by_phone.get(phone) or ())
        result[phone] = compute(
            bucket_counts=buckets,
            # Every cached match row carries a bucket, and the table holds at
            # most one row per (client, property), so the three bucket counts
            # sum to exactly the size of this client's scored id set.
            scored_total=sum(buckets.values()),
            is_scored=lambda record_id, _phone=phone: (_phone, record_id) in scored_pairs,
            manual_ids=manual_ids,
            website_ids=website_ids,
            assigned_ids=assigned_ids,
            completed_ids=completed_ids,
        )
    return result


def _version_of(counts: Dict[str, MatchCounts]) -> str:
    """A hash of the map itself, so a rebuild that finds the same numbers
    keeps the same version and the browser's copy stays valid — the whole
    point of the backstop rebuild is to notice a CHANGE, not to invalidate
    an unchanged answer."""
    payload = json.dumps(
        {phone: counts[phone].model_dump() for phone in sorted(counts)},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:32]
