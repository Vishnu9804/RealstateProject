"""Client-Property Matching orchestrator — the one place that ties
requirement embedding, per-field scoring, and the match-score cache
together into the two operations the Controller layer needs:

  - recompute_for_client: runs the full pipeline for one client (embed
    requirements, score every matchable stored property AND every builder
    project — see _is_matchable and match_candidates.py — and cache the
    result). Called
    automatically whenever that client's requirements change (see
    Service/WhatsAppInquiryHandlingService/client_store.py's
    upsert_client) and manually via the dashboard's Refresh action.
  - get_cached_result: reads ONLY the cache plus each matched property's
    CURRENT display data — no embedding call, no scoring. This is what a
    normal "View Matches" page open uses, so opening the dashboard never
    re-runs the pipeline (the feature's own explicit requirement).

Mirrors Service/WhatsAppDataFetchingService/property_vector_store.py's
in-memory-fallback-vs-Postgres split, so this feature behaves the same way
with or without DATABASE_URL configured, like every other feature
here.
"""

from __future__ import annotations

import heapq
from datetime import datetime, timezone
from typing import Collection, Dict, List, Optional, Set, Tuple

from Database import client_repository, matching_repository
from Database.client_session import is_client_database_configured
from Middleware import step_logger
from Model.ClientPropertyMatchingModel.client_match_result import ClientMatchResult
from Model.ClientPropertyMatchingModel.match_bucket import MatchBucket
from Model.ClientPropertyMatchingModel.match_score import MatchScore
from Model.ClientPropertyMatchingModel.matched_property import MatchedProperty
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty
from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord
from Service.ClientPropertyMatchingService import (
    client_requirement_text_builder,
    match_candidates,
    match_config,
    scoring,
)
from Service.WhatsAppDataFetchingService import embedding_service
from Service.WhatsAppInquiryHandlingService import client_store

# Everything on a client that is NOT a requirement: who they are and how we
# reach them (phone, name, email, photo), the two staff-only notes the
# Inquiries dialog keeps (current address, loan), and the bookkeeping columns
# nobody edits by hand. Changing any of these must leave that client's
# matched properties exactly where they are — correcting a spelling or
# adding a photo is not a new brief.
CLIENT_MATCH_NEUTRAL_FIELDS = frozenset(
    {
        # identity and contact details
        "phone",
        "name",
        "email",
        "has_photo",
        # staff-only notes (Database/client_repository.py's STAFF_DETAIL_FIELDS)
        "current_address",
        "about_loan",
        "notes",
        # Extra, unverified numbers — contact details like `phone` above,
        # never a requirement and never embedded or scored.
        "additional_phones",
        # lifecycle and bookkeeping — written by the pipeline, never by the
        # Edit dialog, and read by nothing in scoring.py
        "status",
        "pending_action",
        "last_follow_up_dates",
        # What was said on the last follow-up. A note about a CONVERSATION,
        # not a brief: it is never embedded, never scored, and changing it
        # must leave this client's matched properties exactly where they
        # are — the same treatment the stamp above gets.
        "follow_up_report",
        "requirement_submission_count",
        "assigned_agent_id",
        "handoff_sent_at",
        "created_at",
        "updated_at",
    }
)

# A requirement field is simply every OTHER field the record has — the rule
# stated as a blacklist, on purpose. Listing the requirement fields by hand
# meant this tuple and the Add/Edit dialog's own field list
# (manual_client_service.DETAIL_FIELDS) had to be kept in step by memory: add
# a new requirement to the form and forget this line, and that requirement
# would be saved and then silently never re-run anyone's matches. Derived,
# a new field is match-relevant until someone deliberately declares it
# neutral above. Sorted so the order is stable across runs.
_REQUIREMENT_FIELDS = tuple(sorted(set(ClientRecord.model_fields) - CLIENT_MATCH_NEUTRAL_FIELDS))

# How many stored properties get scored per recompute — defined, with the
# builder-project ceiling beside it, in match_candidates.py, the one place
# the scored listings are gathered. Kept under this name for anything that
# still reads it from here.
_MAX_PROPERTIES_SCORED = match_candidates.MAX_PROPERTIES

# How many matches one client ever keeps — their best, by the ranking order
# in scoring.ranking_key. Defined with the rest of the engine's tunables in
# match_config.py; re-exported here because this is where callers have always
# read it from.
MAX_MATCHES_PER_CLIENT = match_config.MAX_MATCHES_PER_CLIENT


def _best_matches(scores: List[MatchScore]) -> List[MatchScore]:
    """The highest-RANKED MAX_MATCHES_PER_CLIENT of `scores` (all of them when
    there are fewer) — match score first, confidence as the tie-break, then
    exactness (see scoring.ranking_key). Never an arbitrary hundred.

    heapq rather than a full sort: for a broad brief this can be a couple of
    thousand candidates, and only the top hundred are wanted. The order
    within the result is irrelevant — the dashboard sorts each bucket
    itself."""
    if len(scores) <= MAX_MATCHES_PER_CLIENT:
        return scores
    return heapq.nlargest(MAX_MATCHES_PER_CLIENT, scores, key=scoring.ranking_key)


# In-memory fallback only — untouched whenever DATABASE_URL is set.
_score_cache: Dict[str, List[MatchScore]] = {}
_computed_at_cache: Dict[str, datetime] = {}


def recompute_for_client(phone: str) -> Optional[ClientMatchResult]:
    """Full recompute: every matchable property re-scored, every cached
    score for this client replaced. What a requirements change and the
    dashboard's manual Refresh both run — in those cases the client's own
    vector has (or may have) moved, so every previous score is suspect and
    an incremental pass would be wrong.

    The listings this reads — properties and builder projects alike — come
    from their in-memory caches (see match_candidates.py), so a full rescore
    no longer costs a single row of database traffic."""
    client = client_store.get_client_by_phone(phone)
    if client is None:
        return None

    vector = _embed_requirements(client) if has_requirements(client) else None
    scores, computed_at = recompute_for_client_record(client, vector)
    result = _build_result(client, scores, computed_at)
    step_logger.info(
        f"[Matching] {phone}: recomputed — {len(result.high)} high, {len(result.medium)} medium, "
        f"{len(result.low)} low (out of {len(scores)} scored)."
    )
    return result


def recompute_for_client_record(
    client: ClientRecord,
    vector: Optional[List[float]],
    candidates: Optional[List[EmbeddedProperty]] = None,
    computed_at: Optional[datetime] = None,
    stamp_watermark: bool = True,
) -> Tuple[List[MatchScore], datetime]:
    """The body of a full recompute, over a client record and requirement
    vector the caller already holds.

    Split out of recompute_for_client so a bulk pass over every client (see
    scheduled_recompute_service's one-time engine upgrade) can read the
    client list, their stored vectors and the candidate list ONCE for the
    whole run instead of once per client — the same shape the nightly
    incremental pass already has. `vector` is None/empty for a client with
    no requirements, who correctly ends up with no matches at all.

    `stamp_watermark=False` leaves clients.matches_computed_at to the caller,
    so a bulk pass can write every client's in one statement at the end
    instead of one apiece; it must then write the SAME `computed_at` it
    passed in, or the next nightly run re-examines listings this one already
    took into account."""
    scores: List[MatchScore] = []
    if vector:
        brief = scoring.build_brief(client, vector)
        # score_client_property returns None both for a pair that is not a
        # possible match at all (wrong purpose, wrong kind of property, a BHK
        # the client ruled out, far over a stated maximum — see
        # scoring.is_eligible) and for anything below
        # match_config.MIN_STORED_MATCH_SCORE, so neither ever reaches the
        # cache or the dashboard, in any bucket. A thin brief never removes
        # anything: an incomplete brief lowers CONFIDENCE, which is stored and
        # shown separately, and never the match score itself.
        scores = _best_matches(
            [
                score
                for prop in (match_candidates.get_all() if candidates is None else candidates)
                if _is_matchable(prop) and (score := scoring.score_client_property(prop, brief)) is not None
            ]
        )

    computed_at = computed_at or _now()
    _persist_scores(client.phone, scores, computed_at)
    # The watermark the daily rescore reads: everything up to now has been
    # taken into account for this client, so tomorrow starts from here.
    if stamp_watermark:
        client_store.set_matches_computed_at({client.phone: computed_at})
    return scores, computed_at


def rescore_changed_properties(
    client: ClientRecord,
    changed: List[EmbeddedProperty],
    computed_at: datetime,
    stored_vector: Optional[List[float]] = None,
) -> int:
    """Incremental rescore: only `changed` is looked at, and only those
    properties' cached rows can be affected. Returns how many of them ended
    up as matches.

    This is what the nightly run uses. A client registered on Monday
    afternoon and rescored again that evening does not need Tuesday's run to
    re-compare them against the entire property table — only against what
    arrived or was edited since that evening. Everything else about the
    scoring is identical to a full recompute: same vector, same
    score_property, same cutoffs.

    `stored_vector` is the client's already-saved requirement vector. It is
    reused when present because the requirements have NOT changed (a change
    would have triggered a full recompute at the moment it happened), so
    re-deriving the same vector — and re-saving it — every night would be
    pure waste.
    """
    if not has_requirements(client):
        return 0
    vector = stored_vector if stored_vector else _embed_requirements(client)
    brief = scoring.build_brief(client, vector)
    scores = [
        score
        for prop in changed
        if _is_matchable(prop) and (score := scoring.score_client_property(prop, brief)) is not None
    ]
    # Everything looked at, matchable or not — see
    # matching_repository.merge_matches_for_client for why the merge needs
    # this and not just the winners. A property that has since been pushed
    # into the review queue is "considered but not scored", and its stale
    # cached match has to go.
    considered_ids = {prop.record_id for prop in changed}
    _merge_scores(client.phone, scores, considered_ids, computed_at)
    return len(scores)


def _is_matchable(prop: EmbeddedProperty) -> bool:
    """Only Main and Outsider properties are matched against a client's
    requirements. A property in the review queue (needs_review) is one the
    LLM could extract almost nothing from — no location, no price, no
    configuration (see Model/WhatsAppDataFetchingModel/structured_property.py's
    own comment on the flag) — so there is nothing for scoring to compare,
    and showing it as a "match" would put an empty row in front of a client.

    Applied at BOTH ends on purpose: here, so a flagged property is never
    scored or cached in the first place, and again in _build_result, so a
    score cached before this rule existed can't surface one either. Once a
    human completes a flagged property and files it into Main/Outsider, it
    becomes matchable like any other property and is picked up by that
    client's next recompute."""
    return not prop.needs_review


def is_matchable(prop: EmbeddedProperty) -> bool:
    """Public alias for _is_matchable — the ONE definition of "can this
    property be matched at all", so the broker-requirement side
    (Service/BrokerRequirementService/requirement_matching_service.py)
    applies exactly the same rule rather than a second copy of it that
    could drift. Pure delegation: no behaviour of its own."""
    return _is_matchable(prop)


def display_fields(prop: EmbeddedProperty) -> dict:
    """Public alias for _display_fields, for the same reason as
    is_matchable above: what a MatchedProperty carries about its property
    is decided in one place and read from there by both match surfaces."""
    return _display_fields(prop)


def get_cached_result(phone: str) -> Optional[ClientMatchResult]:
    client = client_store.get_client_by_phone(phone)
    if client is None:
        return None
    scores, computed_at = _read_scores(phone)
    return _build_result(client, scores, computed_at)


def get_match_counts(phone: str) -> Dict[str, int]:
    """AgentManagement feature: how many matches are in each bucket, without
    the expense get_cached_result pays to enrich every match with its
    property's current display fields (property_vector_store.get_all_properties
    loads the whole properties table, embeddings included). Used by the
    Inquiries table's Matches column, which needs this for every visible
    client on every poll — get_cached_result would make that eager cost far
    too high for what is otherwise just a badge with a number on it."""
    scores, _ = _read_scores(phone)
    counts = {"high": 0, "medium": 0, "low": 0}
    for score in scores:
        counts[score.bucket.value] += 1
    return counts


def clear_matches_for_client(phone: str) -> None:
    """Drops every cached score for one client — used when that client is
    deleted outright (the match rows FOREIGN-KEY clients.phone, so they
    have to go first). Wholesale replacement with nothing is already the
    delete path here, in both storage backends."""
    _persist_scores(phone, [], _now())


def drop_property_from_memory_cache(record_id: str) -> None:
    """Forgets one property's cached score for EVERY client — used when that
    property leaves the property database for good by being marked sold out
    (see Service/WhatsAppDataFetchingService/soldout_property_service.py).

    IN-MEMORY FALLBACK ONLY, and called only on that path. With a database
    configured, the equivalent DELETE runs inside the single transaction
    that performs the move (see Database/soldout_property_repository.py's
    move_property_to_soldout), so that the property's removal and the
    disappearance of every match pointing at it can never come apart.
    """
    for phone, scores in _score_cache.items():
        if any(score.record_id == record_id for score in scores):
            _score_cache[phone] = [score for score in scores if score.record_id != record_id]


def drop_edited_property_from_memory_cache(record_id: str, is_protected) -> int:
    """Forgets one EDITED property's cached score for every client except the
    ones `is_protected(phone)` vouches for (assigned to it, or having
    completed a visit to it), and returns how many scores were dropped. The
    in-memory twin of Database/edited_property_match_repository.py's client
    half — see that module for why an edit drops these at all.

    IN-MEMORY FALLBACK ONLY. With a database configured, the equivalent
    DELETE runs there instead, in one statement that never sends a client
    phone number across the wire (which is exactly what the callback here
    would otherwise cost)."""
    dropped = 0
    for phone, scores in _score_cache.items():
        if not any(score.record_id == record_id for score in scores):
            continue
        if is_protected(phone):
            continue
        _score_cache[phone] = [score for score in scores if score.record_id != record_id]
        dropped += 1
    return dropped


def get_scored_property_ids(phone: str) -> set:
    """Every property id currently scored for this client, any bucket —
    the same cheap, cache-only read get_match_counts above does, just the
    ids instead of a per-bucket count. Lets a caller tell "already scored"
    apart from "not scored at all" without loading a single property
    record (see Controller/ClientPropertyMatchingController/
    matching_controller.py's own use: which website-enquired properties
    aren't already reflected in the counts above)."""
    scores, _ = _read_scores(phone)
    return {score.record_id for score in scores}


def get_scores_summary(phone: str) -> Tuple[Dict[str, int], Set[str]]:
    """One client's per-bucket counts AND its scored ids, from a SINGLE
    read of its cached scores.

    get_match_counts and get_scored_property_ids above answer one half each,
    and the counts endpoint needs both — asking them separately read every
    one of that client's cached match rows twice, field_scores JSON and all,
    for two summaries of the same rows. This is those two functions over one
    read; they are kept as they are for callers that genuinely want only one
    of the two."""
    scores, _ = _read_scores(phone)
    counts = {"high": 0, "medium": 0, "low": 0}
    record_ids = set()
    for score in scores:
        counts[score.bucket.value] += 1
        record_ids.add(score.record_id)
    return counts, record_ids


def get_bucket_counts_by_client() -> Dict[str, Dict[str, int]]:
    """phone -> {"high": n, "medium": n, "low": n} for every client that has
    any cached match — the bulk form of get_match_counts above, and what the
    Inquiries table's counts are actually built from now.

    Same numbers, one read instead of one per client. The per-client version
    is kept for the single-client endpoint (and for anything that only ever
    asks about one), but a table of hundreds of rows must never be served by
    asking hundreds of separate questions."""
    if is_client_database_configured():
        return matching_repository.get_bucket_counts_by_client()
    counts: Dict[str, Dict[str, int]] = {}
    for phone, scores in _score_cache.items():
        if not scores:
            continue
        per_bucket = {"high": 0, "medium": 0, "low": 0}
        for score in scores:
            per_bucket[score.bucket.value] += 1
        counts[phone] = per_bucket
    return counts


def get_scored_pairs(pairs: Collection[Tuple[str, str]]) -> Set[Tuple[str, str]]:
    """Which of these (phone, property record id) pairs are currently
    scored — see matching_repository.get_scored_pairs for why the bulk
    counts ask the question this way instead of loading every scored id."""
    if is_client_database_configured():
        return matching_repository.get_scored_pairs(pairs)
    wanted = {pair for pair in pairs}
    if not wanted:
        return set()
    found: Set[Tuple[str, str]] = set()
    by_phone: Dict[str, Set[str]] = {}
    for phone, record_id in wanted:
        by_phone.setdefault(phone, set()).add(record_id)
    for phone, record_ids in by_phone.items():
        scored = {score.record_id for score in _score_cache.get(phone, [])}
        found.update((phone, record_id) for record_id in record_ids & scored)
    return found


def has_requirements(client: ClientRecord) -> bool:
    return any(
        [
            client.purpose,
            client.property_type,
            client.bhk,
            client.budget_min_inr is not None,
            client.budget_max_inr is not None,
            client.preferred_areas,
            client.additional_requirements,
            client.property_sizes,
            client.furnishing,
        ]
    )


def requirement_fields_changed(previous: Optional[ClientRecord], current: ClientRecord) -> bool:
    """Whether any REQUIREMENT field differs between the two records —
    deliberately narrower than "the record changed at all", so a
    name/email-only edit doesn't trigger a pointless recompute. See
    client_store.upsert_client, the single choke point every client write
    goes through."""
    if previous is None:
        return any(getattr(current, name) is not None for name in _REQUIREMENT_FIELDS)
    return any(getattr(previous, name) != getattr(current, name) for name in _REQUIREMENT_FIELDS)


def _embed_requirements(client: ClientRecord) -> List[float]:
    text = client_requirement_text_builder.build_requirement_text(client)
    if not text:
        return []
    vector = embedding_service.embed_text(text)
    if is_client_database_configured():
        client_repository.save_requirement_embedding(client.phone, vector)
    return vector


def _persist_scores(phone: str, scores: List[MatchScore], computed_at: datetime) -> None:
    if is_client_database_configured():
        matching_repository.replace_matches_for_client(phone, scores)
    else:
        _score_cache[phone] = scores
        _computed_at_cache[phone] = computed_at


def _merge_scores(
    phone: str,
    scores: List[MatchScore],
    considered_ids: Set[str],
    computed_at: datetime,
) -> None:
    """Applies an incremental pass: the considered properties' rows are
    replaced by whatever they scored this time (or removed if they no
    longer score at all), and every other cached match survives untouched."""
    if is_client_database_configured():
        matching_repository.merge_matches_for_client(
            phone, scores, considered_ids, computed_at, keep_best=MAX_MATCHES_PER_CLIENT
        )
        return
    kept = [score for score in _score_cache.get(phone, []) if score.record_id not in considered_ids]
    _score_cache[phone] = _best_matches(kept + scores)
    _computed_at_cache[phone] = computed_at


def _read_scores(phone: str) -> tuple[List[MatchScore], Optional[datetime]]:
    if is_client_database_configured():
        return matching_repository.get_matches_for_client(phone)
    return _score_cache.get(phone, []), _computed_at_cache.get(phone)


def _build_result(client: ClientRecord, scores: List[MatchScore], computed_at: Optional[datetime]) -> ClientMatchResult:
    # Display fields only — nothing is scored here, so a builder project
    # without a vector yet must not trigger the embedding model on a plain
    # "View matches" open.
    properties_by_id = {
        prop.record_id: prop for prop in match_candidates.get_all(ensure_embeddings=False) if _is_matchable(prop)
    }
    high: List[MatchedProperty] = []
    medium: List[MatchedProperty] = []
    low: List[MatchedProperty] = []
    buckets = {MatchBucket.HIGH: high, MatchBucket.MEDIUM: medium, MatchBucket.LOW: low}

    for match in scores:
        prop = properties_by_id.get(match.record_id)
        if prop is None:
            continue  # property was deleted since this was cached — skip, don't error
        enriched = MatchedProperty(**match.model_dump(), **_display_fields(prop))
        buckets[match.bucket].append(enriched)

    # Ranked the way the engine ranks — match score first, then confidence,
    # then exactness (scoring.ranking_key) — so what the broker reads top to
    # bottom is the engine's own order and not a second, slightly different
    # one.
    for group in (high, medium, low):
        group.sort(key=scoring.ranking_key, reverse=True)

    return ClientMatchResult(
        phone=client.phone,
        client_name=client.name,
        has_requirements=has_requirements(client),
        computed_at=computed_at,
        high=high,
        medium=medium,
        low=low,
    )


def _display_fields(prop: EmbeddedProperty) -> dict:
    return {
        "property_type": prop.property_type,
        "bhk": prop.bhk,
        "unit_no": prop.unit_no,
        "society_name": prop.society_name,
        "area_name": prop.area_name,
        "address": prop.address,
        "price_text": prop.price_text,
        "price_amount_inr": prop.price_amount_inr,
        "listing_type": prop.listing_type,
        "area_sqft": prop.area_sqft,
        "area_vaar": prop.area_vaar,
        "furnishing": prop.furnishing,
        "contact_name": prop.contact_name,
        # Both shapes: the full list for the dialogs that show every number,
        # and the primary on its own for everything that shows one line.
        # Neither is stored on the match row — this whole dict is rebuilt
        # from the live listing on read (see this function's caller), which
        # is what lets a match card show a corrected number without the
        # match itself being re-scored.
        "contact_phones": list(prop.contact_phones),
        "contact_phone": prop.contact_phone,
        "description": prop.description,
        "review_status": prop.review_status,
        "needs_review": prop.needs_review,
        "property_source": match_candidates.source_of(prop),
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)
