"""Client-Property Matching orchestrator — the one place that ties
requirement embedding, per-field scoring, and the match-score cache
together into the two operations the Controller layer needs:

  - recompute_for_client: runs the full pipeline for one client (embed
    requirements, score every stored property, cache the result). Called
    automatically whenever that client's requirements change (see
    Service/WhatsAppInquiryHandlingService/client_store.py's
    upsert_client) and manually via the dashboard's Refresh action.
  - get_cached_result: reads ONLY the cache plus each matched property's
    CURRENT display data — no embedding call, no scoring. This is what a
    normal "View Matches" page open uses, so opening the dashboard never
    re-runs the pipeline (the feature's own explicit requirement).

Mirrors Service/WhatsAppDataFetchingService/property_vector_store.py's
in-memory-fallback-vs-Postgres split, so this feature behaves the same way
with or without CLIENT_DATABASE_URL configured, like every other feature
here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

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

# In-memory fallback only — untouched whenever CLIENT_DATABASE_URL is set.
_score_cache: Dict[str, List[MatchScore]] = {}
_computed_at_cache: Dict[str, datetime] = {}


def recompute_for_client(phone: str) -> Optional[ClientMatchResult]:
    client = client_store.get_client_by_phone(phone)
    if client is None:
        return None

    scores: List[MatchScore] = []
    if has_requirements(client):
        vector = _embed_requirements(client)
        # score_property returns None for anything below scoring.LOW_CUTOFF
        # (currently 80%) — filtered out here so those never reach the
        # cache or the dashboard, in any bucket.
        scores = [
            score
            for prop in property_vector_store.get_all_properties(limit=_MAX_PROPERTIES_SCORED)
            if (score := scoring.score_property(client, prop, vector)) is not None
        ]

    computed_at = _now()
    _persist_scores(phone, scores, computed_at)
    result = _build_result(client, scores, computed_at)
    step_logger.info(
        f"[Matching] {phone}: recomputed — {len(result.high)} high, {len(result.medium)} medium, "
        f"{len(result.low)} low (out of {len(scores)} scored)."
    )
    return result


def get_cached_result(phone: str) -> Optional[ClientMatchResult]:
    client = client_store.get_client_by_phone(phone)
    if client is None:
        return None
    scores, computed_at = _read_scores(phone)
    return _build_result(client, scores, computed_at)


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


def _read_scores(phone: str) -> tuple[List[MatchScore], Optional[datetime]]:
    if is_client_database_configured():
        return matching_repository.get_matches_for_client(phone)
    return _score_cache.get(phone, []), _computed_at_cache.get(phone)


def _build_result(client: ClientRecord, scores: List[MatchScore], computed_at: Optional[datetime]) -> ClientMatchResult:
    properties_by_id = {
        prop.record_id: prop for prop in property_vector_store.get_all_properties(limit=_MAX_PROPERTIES_SCORED)
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
