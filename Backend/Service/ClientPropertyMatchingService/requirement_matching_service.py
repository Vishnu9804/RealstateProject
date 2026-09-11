"""Matching for the DEMAND side: one broker requirement in, the same
high/medium/low matched properties out that a client inquiry already gets.

THE WHOLE POINT: IT IS THE SAME SCORING

A broker requirement and a client inquiry are the same thing said by two
different people — "someone wants a 3 BHK in Vesu under 90L". So this module
contains no scoring logic of its own. It adapts a StructuredRequirement into
the ClientRecord shape scoring.score_property already takes (see
_as_pseudo_client) and hands it to the existing, unmodified engine: the same
critical gate, the same budget curve, the same cutoffs, the same
MatchBucket. A requirement and an equivalent client inquiry therefore score
identically, by construction rather than by coincidence — and any future
tuning of scoring.py lands on both at once.

WHY IT IS COMPUTED ON DEMAND, NOT CACHED

The client side caches scores in a database table because it has to: match
counts are shown for every row of the Inquiries table on every poll, and the
nightly job re-scores everybody. A requirement has neither. Nothing displays
a requirement's match count, and the only thing that ever asks for these
numbers is a human opening the dialog. Scoring against the in-memory
property snapshot (Service/WhatsAppDataFetchingService/property_snapshot.py)
costs no database traffic at all, so an on-demand pass buys the same answer
with no new table, no new migration, no cache to invalidate when a property
is edited, and nothing added to the nightly run that could slow it down or
fail it.

The one cost that is NOT free is the requirement's own embedding — a real
call out to the embedding provider. That is memoised per requirement against
the exact text it was built from (see _requirement_vector), so reopening the
same dialog, or opening it after an unrelated property arrived, does not pay
for it again, while editing the requirement's fields correctly does.

NOTHING HERE WRITES ANYTHING. No cached scores, no stored vector (unlike
matching_service._embed_requirements, which persists a client's vector on
the client row — a requirement has no such column and must not acquire one
as a side effect of being looked at).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from Middleware import step_logger
from Model.ClientPropertyMatchingModel.match_bucket import MatchBucket
from Model.ClientPropertyMatchingModel.match_score import MatchScore
from Model.ClientPropertyMatchingModel.matched_property import MatchedProperty
from Model.ClientPropertyMatchingModel.requirement_match_result import RequirementMatchResult
from Model.WhatsAppDataFetchingModel.broker_requirement import StructuredRequirement
from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord
from Service.ClientPropertyMatchingService import (
    client_requirement_text_builder,
    matching_service,
    scoring,
)
from Service.WhatsAppDataFetchingService import embedding_service, property_vector_store, requirement_store

# Same ceiling the client side scores under, and for the same reason — see
# matching_service's own comment on it. Read from there rather than
# re-declared, so the two can never be tuned apart.
_MAX_PROPERTIES_SCORED = matching_service._MAX_PROPERTIES_SCORED

# record_id -> (the exact text that was embedded, the resulting vector).
# Keyed on the TEXT, not just the id, so an edited requirement re-embeds
# instead of silently reusing the vector of what it used to say. Bounded for
# the same reason every other in-process cache here is.
_MAX_CACHED_VECTORS = 500
_vector_cache: Dict[str, Tuple[str, List[float]]] = {}


def get_matches_for_requirement(record_id: str) -> Optional[RequirementMatchResult]:
    """Scores every matchable stored property against one broker
    requirement. Returns None only when no requirement with this record_id
    exists (the controller turns that into a 404) — a requirement that
    simply has nothing to match on comes back as a real result with
    has_requirements=False and three empty buckets, which is a different
    answer and reads differently in the dialog."""
    requirement = requirement_store.get_requirement(record_id)
    if requirement is None:
        return None

    pseudo_client = _as_pseudo_client(requirement)
    scores: List[MatchScore] = []
    if matching_service.has_requirements(pseudo_client):
        vector = _requirement_vector(requirement, pseudo_client)
        # score_property returns None below scoring.LOW_CUTOFF, so anything
        # under 70% never reaches the dialog in any bucket — identical to
        # the client side.
        scores = [
            score
            for prop in property_vector_store.get_all_properties(limit=_MAX_PROPERTIES_SCORED)
            if matching_service.is_matchable(prop)
            and (score := scoring.score_property(pseudo_client, prop, vector)) is not None
        ]

    result = _build_result(requirement, pseudo_client, scores)
    step_logger.info(
        f"[Matching] requirement {record_id}: {len(result.high)} high, {len(result.medium)} medium, "
        f"{len(result.low)} low (out of {len(scores)} scored)."
    )
    return result


def forget_requirement(record_id: str) -> None:
    """Drops the memoised vector for one requirement — called when it is
    deleted, so a record_id that no longer exists leaves nothing cached
    behind it. Editing needs no call here: the cache is keyed on the text
    that was embedded, so changed fields miss on their own."""
    _vector_cache.pop(record_id, None)


def _as_pseudo_client(requirement: StructuredRequirement) -> ClientRecord:
    """The adapter this whole module exists for: a StructuredRequirement
    expressed in the ClientRecord fields scoring.py reads.

    Field by field, and why:

      - purpose: derived from listing_type. A requirement says "Sale" or
        "Rent" (what the broker wants to do); a client says "buy" or "rent".
        scoring's purpose gate speaks the client's vocabulary, so the
        translation happens here and the gate stays untouched.
      - property_type: requirement_type — the same value space
        StructuredProperty.property_type uses, which is exactly what
        normalization.property_type_gate compares against.
      - preferred_areas: the full list, comma-joined, because scoring's
        location score splits a client's own free-text field on commas and
        slashes. area_name is only the first of that list (see
        StructuredRequirement's own comment), so it is a fallback for the
        empty-list case, never a replacement.
      - additional_requirements: the free text a requirement carries that
        has no dedicated field on ClientRecord — the society asked for by
        name, the furnishing asked for, the description. It feeds the
        semantic half of the score (see
        client_requirement_text_builder.build_requirement_text), which is
        where an unstructured "needs parking, high floor" belongs.
      - carpet_area_min/max has NO counterpart on ClientRecord and is
        therefore not scored — the same as today for a client, who has
        nowhere to state a wanted size either. Deliberately not smuggled in
        as free text, which would let a number the scorer cannot compare
        drag the semantic component around unpredictably.

    `phone` is required by the model and is filled with the requirement's
    own sender number. Nothing reads it on this path (no cache is keyed by
    it, no row is written for it) — it is here so the object is valid, and
    it is the truthful value rather than a placeholder.
    """
    areas = [area for area in (requirement.preferred_areas or []) if area and area.strip()]
    if not areas and requirement.area_name:
        areas = [requirement.area_name]
    extra = [requirement.society_name, requirement.furnishing, requirement.description]
    return ClientRecord(
        phone=requirement.sender_phone,
        name=requirement.contact_name or requirement.sender_saved_name or requirement.sender_name,
        purpose="rent" if requirement.listing_type == "Rent" else "buy",
        property_type=requirement.requirement_type,
        bhk=requirement.bhk,
        budget_min_inr=requirement.budget_min_inr,
        budget_max_inr=requirement.budget_max_inr,
        preferred_areas=", ".join(areas) if areas else None,
        additional_requirements=" | ".join(part.strip() for part in extra if part and part.strip()) or None,
    )


def _requirement_vector(requirement: StructuredRequirement, pseudo_client: ClientRecord) -> List[float]:
    """The requirement's embedding, built from exactly the same canonical
    text a client's is (client_requirement_text_builder) so the two land in
    the same semantic space as the property vectors they are compared
    against — that shared construction, not merely the shared model, is what
    makes the cosine similarity in scoring's semantic component mean
    anything.

    Memoised as record_id -> (text, vector): a cache hit requires the text to
    be identical, so an edit re-embeds and an unrelated property arriving
    does not."""
    text = client_requirement_text_builder.build_requirement_text(pseudo_client)
    if not text:
        return []
    cached = _vector_cache.get(requirement.record_id)
    if cached is not None and cached[0] == text:
        return cached[1]
    vector = embedding_service.embed_text(text)
    if len(_vector_cache) >= _MAX_CACHED_VECTORS:
        _vector_cache.pop(next(iter(_vector_cache)), None)
    _vector_cache[requirement.record_id] = (text, vector)
    return vector


def _build_result(
    requirement: StructuredRequirement, pseudo_client: ClientRecord, scores: List[MatchScore]
) -> RequirementMatchResult:
    """Joins each score against the property's CURRENT display fields, the
    same way matching_service's own result builder does — a property edited
    or moved between Main/Outsider since it was scored shows its live
    values, never a snapshot."""
    properties_by_id = {
        prop.record_id: prop
        for prop in property_vector_store.get_all_properties(limit=_MAX_PROPERTIES_SCORED)
        if matching_service.is_matchable(prop)
    }
    high: List[MatchedProperty] = []
    medium: List[MatchedProperty] = []
    low: List[MatchedProperty] = []
    buckets = {MatchBucket.HIGH: high, MatchBucket.MEDIUM: medium, MatchBucket.LOW: low}

    for match in scores:
        prop = properties_by_id.get(match.record_id)
        if prop is None:
            continue  # deleted between scoring and this join — skip, don't error
        buckets[match.bucket].append(
            MatchedProperty(**match.model_dump(), **matching_service.display_fields(prop))
        )

    for group in (high, medium, low):
        group.sort(key=lambda m: m.score, reverse=True)

    return RequirementMatchResult(
        record_id=requirement.record_id,
        requirement_summary=_summarize(requirement),
        has_requirements=matching_service.has_requirements(pseudo_client),
        computed_at=datetime.now(timezone.utc),
        high=high,
        medium=medium,
        low=low,
    )


def _summarize(requirement: StructuredRequirement) -> str:
    areas = [area for area in (requirement.preferred_areas or []) if area and area.strip()]
    if not areas and requirement.area_name:
        areas = [requirement.area_name]
    parts = [
        " ".join(part for part in (requirement.bhk, requirement.requirement_type) if part) or None,
        "to rent" if requirement.listing_type == "Rent" else "to buy",
        ", ".join(areas) if areas else None,
    ]
    return " · ".join(part for part in parts if part)
