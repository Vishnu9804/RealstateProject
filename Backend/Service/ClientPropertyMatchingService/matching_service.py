"""Client-Property Matching orchestrator — the one place that ties
requirement embedding, per-field scoring, and the match-score cache
together into the two operations the Controller layer needs:

  - recompute_for_client: runs the full pipeline for one client (embed
    requirements, score every matchable stored property — see
    _is_matchable — and cache the result). Called
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

from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from Database import client_repository, matching_repository
from Database.client_session import is_client_database_configured
from Middleware import step_logger
from Model.ClientPropertyMatchingModel.client_match_result import ClientMatchResult
from Model.ClientPropertyMatchingModel.match_bucket import MatchBucket
from Model.ClientPropertyMatchingModel.match_score import MatchScore
from Model.ClientPropertyMatchingModel.matched_property import MatchedProperty
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty
from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord
from Service.ClientPropertyMatchingService import client_requirement_text_builder, scoring
from Service.WhatsAppDataFetchingService import embedding_service, property_vector_store
from Service.WhatsAppInquiryHandlingService import client_store

_REQUIREMENT_FIELDS = (
    "purpose",
    "property_type",
    "bhk",
    "budget_min_inr",
    "budget_max_inr",
    "preferred_areas",
    "additional_requirements",
)

# How many stored properties get scored per recompute. The property table
# is WhatsApp-sourced and, at this project's current scale, realistically
# in the hundreds to low thousands — scoring every row directly in Python
# is simpler than adding a pgvector top-K pre-filter and fast enough at
# this size. Revisit only if property_vector_store.get_property_count()
# grows well past this.
_MAX_PROPERTIES_SCORED = 5000

# In-memory fallback only — untouched whenever DATABASE_URL is set.
_score_cache: Dict[str, List[MatchScore]] = {}
_computed_at_cache: Dict[str, datetime] = {}


def recompute_for_client(phone: str) -> Optional[ClientMatchResult]:
    """Full recompute: every matchable property re-scored, every cached
    score for this client replaced. What a requirements change and the
    dashboard's manual Refresh both run — in those cases the client's own
    vector has (or may have) moved, so every previous score is suspect and
    an incremental pass would be wrong.

    The property list this reads comes from the in-memory snapshot, so a
    full rescore no longer costs a single row of database traffic."""
    client = client_store.get_client_by_phone(phone)
    if client is None:
        return None

    scores: List[MatchScore] = []
    if has_requirements(client):
        vector = _embed_requirements(client)
        # score_property returns None for anything below scoring.LOW_CUTOFF
        # (currently 70%) — filtered out here so those never reach the
        # cache or the dashboard, in any bucket.
        scores = [
            score
            for prop in property_vector_store.get_all_properties(limit=_MAX_PROPERTIES_SCORED)
            if _is_matchable(prop) and (score := scoring.score_property(client, prop, vector)) is not None
        ]

    computed_at = _now()
    _persist_scores(phone, scores, computed_at)
    # The watermark the daily rescore reads: everything up to now has been
    # taken into account for this client, so tomorrow starts from here.
    client_store.set_matches_computed_at({phone: computed_at})
    result = _build_result(client, scores, computed_at)
    step_logger.info(
        f"[Matching] {phone}: recomputed — {len(result.high)} high, {len(result.medium)} medium, "
        f"{len(result.low)} low (out of {len(scores)} scored)."
    )
    return result


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
    scores = [
        score
        for prop in changed
        if _is_matchable(prop) and (score := scoring.score_property(client, prop, vector)) is not None
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
    (Service/ClientPropertyMatchingService/requirement_matching_service.py)
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
        ]
    )


def requirement_fields_changed(previous: Optional[ClientRecord], current: ClientRecord) -> bool:
    """Whether any REQUIREMENT field differs between the two records —
    deliberately narrower than "the record changed at all", so a
    pending_action toggle or a name/email-only edit doesn't trigger a
    pointless recompute. See client_store.upsert_client, the single choke
    point every client write goes through."""
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
        matching_repository.merge_matches_for_client(phone, scores, considered_ids, computed_at)
        return
    kept = [score for score in _score_cache.get(phone, []) if score.record_id not in considered_ids]
    _score_cache[phone] = kept + scores
    _computed_at_cache[phone] = computed_at


def _read_scores(phone: str) -> tuple[List[MatchScore], Optional[datetime]]:
    if is_client_database_configured():
        return matching_repository.get_matches_for_client(phone)
    return _score_cache.get(phone, []), _computed_at_cache.get(phone)


def _build_result(client: ClientRecord, scores: List[MatchScore], computed_at: Optional[datetime]) -> ClientMatchResult:
    properties_by_id = {
        prop.record_id: prop
        for prop in property_vector_store.get_all_properties(limit=_MAX_PROPERTIES_SCORED)
        if _is_matchable(prop)
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

    for group in (high, medium, low):
        group.sort(key=lambda m: m.score, reverse=True)

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
        "society_name": prop.society_name,
        "area_name": prop.area_name,
        "address": prop.address,
        "price_text": prop.price_text,
        "price_amount_inr": prop.price_amount_inr,
        "listing_type": prop.listing_type,
        "carpet_area_sqft": prop.carpet_area_sqft,
        "carpet_area_unit": prop.carpet_area_unit,
        "contact_name": prop.contact_name,
        "contact_phone": prop.contact_phone,
        "description": prop.description,
        "review_status": prop.review_status,
        "needs_review": prop.needs_review,
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)
