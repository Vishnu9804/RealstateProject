"""Client-Property match scoring — one (client, property) pair in, one
MatchScore out. Deliberately NOT a fixed weighted average of every field:

  final_score = critical_gate × soft_score

`critical_gate` is the product of the purpose (buy/rent) and property-type
compatibility factors — both in [0, 1], both defaulting to 1.0 when the
client didn't state a preference (an unfilled field is never used as a
filter, per the feature spec). A mismatch on either is a different KIND of
wrong than "somewhat over budget": a rental listing for a buyer, or a plot
for someone who wants a flat, should never hide behind otherwise-good
scores the way a fixed weighted sum would let it. The gate multiplies the
soft score down instead of hard-excluding the candidate outright, so a
severe mismatch still lands low and visible (Low bucket) rather than
silently disappearing.

`soft_score` is an evidence-weighted average of the remaining fields —
budget, location, BHK, and a whole-vector semantic sanity check: any field
that isn't comparable on both sides scores None and is excluded from both
the numerator and the weight total, never treated as a match or a mismatch.
`evidence_ratio` (how much of the total weight was actually backed by data)
drives the `is_partial_match` flag the dashboard shows alongside the bucket.

Only properties that are actually matchable ever reach this — a property in
the review queue is filtered out upstream (see matching_service._is_matchable).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from Model.ClientPropertyMatchingModel.match_bucket import MatchBucket
from Model.ClientPropertyMatchingModel.match_score import MatchScore
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty
from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord
from Service.ClientPropertyMatchingService import normalization

# Soft-field weights — budget carries the most weight (per the feature
# spec's heavy emphasis on graduated budget proximity), location second
# (a wrong area should visibly drag an otherwise-strong match down),
# BHK third, and the whole-vector semantic check as a low-weight tie
# breaker/sanity signal only.
_SOFT_WEIGHTS = {"budget": 0.40, "location": 0.30, "bhk": 0.20, "semantic": 0.10}

HIGH_CUTOFF = 0.90
MEDIUM_CUTOFF = 0.80
# Below this, a property isn't shown as a match at all (score_property
# returns None) — not even as "Low". Per the feature owner's explicit
# requirement: only 70-100% should ever reach the dashboard.
LOW_CUTOFF = 0.70
PARTIAL_EVIDENCE_CUTOFF = 0.6

# Calibration points for graduated budget proximity — (ratio-away-from-the
# nearest bound, score). Piecewise-linear between them, with enough anchor
# points for a smooth curve matching the feature spec's own worked examples (₹89L/90L max -> ~1.0, ₹92L -> ~0.88, ₹96L -> ~somewhat
# outside, ₹1.10cr -> poor fit, ₹1.5cr -> very poor fit).
_OVER_BUDGET_ANCHORS: Tuple[Tuple[float, float], ...] = ((0.0, 1.0), (0.05, 0.85), (0.15, 0.55), (0.30, 0.25), (0.60, 0.05))
_UNDER_BUDGET_ANCHORS: Tuple[Tuple[float, float], ...] = ((0.0, 1.0), (0.15, 0.85), (0.40, 0.65), (1.0, 0.5))

# Size — the optional per-type size a client can give on the requirements
# form ("Flat: 1200 sqft", "Bungalow: 200 vaar"). Scored only when the
# client gave one that could be read, and weighted below everything else:
# most people only have a rough idea, so a size miss nudges a property down
# the list rather than knocking it out. Its curve is gentler than the
# budget's for the same reason, and never reaches zero.
_SIZE_WEIGHT = 0.08
_SIZE_ANCHORS: Tuple[Tuple[float, float], ...] = ((0.0, 1.0), (0.10, 0.85), (0.25, 0.6), (0.50, 0.35), (1.0, 0.2))

# Furnishing — "Fully furnished" / "Semi furnished" / "Unfurnished", stated on
# the requirements form (and, for a broker requirement, extracted from the
# message). The LOWEST-weighted field here, on purpose: furnishing is the
# easiest thing about a property to change, so it is a tie-breaker between
# otherwise comparable listings, never something that moves a genuinely good
# match out of its bucket. Its curve lives in normalization.furnishing_score.
#
# Like the size weight above, it is added to the weight total ONLY when both
# sides actually state a furnishing (normalization.furnishing_score returns
# None otherwise). So every client and every requirement that says nothing
# about furnishing — which is all of them until someone fills the new field
# in — scores byte-for-byte what it always did, evidence_ratio included.
_FURNISHING_WEIGHT = 0.06

SizeRange = Tuple[Optional[float], Optional[float]]


def score_property(client: ClientRecord, prop: EmbeddedProperty, client_vector: List[float]) -> Optional[MatchScore]:
    """Returns None (not a MatchScore) when the final score falls below
    LOW_CUTOFF — the caller (matching_service.py) filters these out, so a
    property scoring under 80% never reaches the dashboard at all, in any
    bucket.

    client.property_type is read whole ("first type is the main one"),
    which is what a broker requirement means by a comma list. A client's
    own multi-select goes through score_client_property instead."""
    return _score(
        prop,
        _purpose_gate(client.purpose, prop.listing_type),
        normalization.property_type_gate(client.property_type, prop.property_type),
        _soft_field_scores(client, prop, client_vector),
        furnishing=normalization.furnishing_score(client.furnishing, prop.furnishing),
    )


def client_type_plan(client: ClientRecord) -> List[Tuple[str, Optional[SizeRange]]]:
    """Each property type the client picked, with the size range they gave
    for it (None when they gave none, or nothing readable). Worked out ONCE
    per client and handed to score_client_property for every property,
    rather than re-parsing the same text thousands of times per recompute."""
    return [
        (group, normalization.parse_size_requirement(normalization.size_for(client.property_sizes, group), group))
        for group in normalization.split_type_groups(client.property_type)
    ]


def score_client_property(
    client: ClientRecord,
    prop: EmbeddedProperty,
    client_vector: List[float],
    plan: List[Tuple[str, Optional[SizeRange]]],
) -> Optional[MatchScore]:
    """score_property for an inquiry client, who may have picked several
    property types ("Flat, Bungalow") and a size for each.

    Every type is an equal preference, so the property is scored against
    each one on its own — that type's gate and that type's size, everything
    else shared — and keeps its best result, tagged with the type it was
    for (`matched_type`, only when there is more than one to choose
    between). A tie goes to the type naming this property exactly, then to
    the one picked first.

    A client with one type and no readable size (every client stored
    before this existed) takes score_property itself, so their scores are
    exactly what they always were."""
    if len(plan) <= 1 and not (plan and plan[0][1]):
        return score_property(client, prop, client_vector)

    purpose_factor = _purpose_gate(client.purpose, prop.listing_type)
    soft = _soft_field_scores(client, prop, client_vector)
    prop_token = normalization.canonical_type_token(prop.property_type) if prop.property_type else ""
    area_sqft = normalization.property_area_sqft(prop.area_sqft, prop.area_vaar)
    # Furnishing is stated once for the whole brief, not per type, so it is
    # worked out once here rather than inside the loop below.
    furnishing = normalization.furnishing_score(client.furnishing, prop.furnishing)

    best: Optional[MatchScore] = None
    best_key = None
    for index, (group, wanted) in enumerate(plan):
        score = _score(
            prop,
            purpose_factor,
            normalization.property_type_gate(group, prop.property_type),
            soft,
            size=_size_score(wanted, area_sqft) if wanted else None,
            size_stated=wanted is not None,
            furnishing=furnishing,
        )
        if score is None:
            continue
        key = (score.score, prop_token in normalization.split_client_property_types(group), -index)
        if best_key is None or key > best_key:
            best, best_key = score, key
            if len(plan) > 1:
                score.matched_type = group
    return best


def _soft_field_scores(client: ClientRecord, prop: EmbeddedProperty, client_vector: List[float]) -> Dict[str, Optional[float]]:
    return {
        "budget": _budget_score(client.budget_min_inr, client.budget_max_inr, prop.price_amount_inr),
        "location": _location_score(client.preferred_areas, prop.area_name, prop.address),
        "bhk": normalization.bhk_score(client.bhk, prop.bhk),
        "semantic": _semantic_score(client_vector, prop.embedding),
    }


def _score(
    prop: EmbeddedProperty,
    purpose_factor: float,
    type_factor: float,
    soft: Dict[str, Optional[float]],
    size: Optional[float] = None,
    size_stated: bool = False,
    furnishing: Optional[float] = None,
) -> Optional[MatchScore]:
    critical_gate = purpose_factor * type_factor

    field_scores: Dict[str, Optional[float]] = dict(soft)
    extra_weights: Dict[str, float] = {}
    if size_stated:
        field_scores["size"] = size
        # Weighed only when this property has an area to compare. Plenty of
        # listings don't, and an optional preference the client was unsure
        # of anyway must not flag all of those as "Partial data".
        if size is not None:
            extra_weights["size"] = _SIZE_WEIGHT
    # Already None unless BOTH sides state a furnishing (see
    # normalization.furnishing_score), so a brief that says nothing about it
    # adds no field and no weight — and scores exactly as it did before this
    # field existed.
    if furnishing is not None:
        field_scores["furnishing"] = furnishing
        extra_weights["furnishing"] = _FURNISHING_WEIGHT
    weights = {**_SOFT_WEIGHTS, **extra_weights} if extra_weights else _SOFT_WEIGHTS

    comparable_weight = sum(weights[name] for name, score in field_scores.items() if score is not None)
    total_weight = sum(weights.values())
    evidence_ratio = comparable_weight / total_weight if total_weight else 0.0

    if comparable_weight == 0:
        soft_score = 0.5  # nothing comparable at all — neutral, not zero
    else:
        soft_score = (
            sum(weights[name] * score for name, score in field_scores.items() if score is not None)
            / comparable_weight
        )

    final_score = critical_gate * soft_score

    if final_score >= HIGH_CUTOFF:
        bucket = MatchBucket.HIGH
    elif final_score >= MEDIUM_CUTOFF:
        bucket = MatchBucket.MEDIUM
    elif final_score >= LOW_CUTOFF:
        bucket = MatchBucket.LOW
    else:
        return None

    field_scores["purpose_gate"] = purpose_factor
    field_scores["property_type_gate"] = type_factor

    return MatchScore(
        record_id=prop.record_id,
        score=round(final_score, 4),
        bucket=bucket,
        evidence_ratio=round(evidence_ratio, 4),
        is_partial_match=evidence_ratio < PARTIAL_EVIDENCE_CUTOFF,
        property_category=_category_of(prop),
        field_scores=field_scores,
        reason=_build_reason(purpose_factor, type_factor, evidence_ratio, size, furnishing),
    )


def _category_of(prop: EmbeddedProperty) -> str:
    if prop.needs_review:
        return "needs_review"
    return "main" if prop.review_status == "accepted" else "outsider"


def _build_reason(
    purpose_factor: float,
    type_factor: float,
    evidence_ratio: float,
    size: Optional[float] = None,
    furnishing: Optional[float] = None,
) -> str:
    notes: List[str] = []
    if purpose_factor < 0.5:
        notes.append("purpose (buy/rent) does not match")
    if type_factor < 0.3:
        notes.append("property type is a poor fit")
    elif type_factor < 0.9:
        notes.append("property type is a partial fit")
    if size is not None and size < 0.6:
        notes.append("size is outside the preferred range")
    # Said, but said last and said mildly — it is the lowest-weighted field
    # here and never on its own a reason to look elsewhere.
    if furnishing is not None and furnishing < 1.0:
        notes.append("furnishing is not what was asked for")
    if evidence_ratio < PARTIAL_EVIDENCE_CUTOFF:
        notes.append("limited data available for a full comparison")
    return "; ".join(notes) if notes else "Matches on the fields that were compared."


# --- critical gate fields ------------------------------------------------


def _purpose_gate(client_purpose: Optional[str], listing_type: Optional[str]) -> float:
    if not client_purpose:
        return 1.0
    normalized = client_purpose.strip().lower()
    if normalized not in ("buy", "rent"):
        return 1.0  # e.g. "sell" or unrecognized free text — don't guess, don't penalize
    if not listing_type:
        return 0.5  # unknown on the property side
    wants_sale = normalized == "buy"
    is_sale = listing_type == "Sale"
    return 1.0 if wants_sale == is_sale else 0.08


# --- soft fields -----------------------------------------------------------


def _budget_score(budget_min: Optional[float], budget_max: Optional[float], price: Optional[float]) -> Optional[float]:
    if price is None or (budget_min is None and budget_max is None):
        return None
    lo = budget_min if budget_min is not None else 0.0
    hi = budget_max if budget_max is not None else float("inf")
    if lo <= price <= hi:
        return 1.0
    if price > hi:
        return _interpolate((price - hi) / hi, _OVER_BUDGET_ANCHORS) if hi > 0 else 1.0
    return _interpolate((lo - price) / lo, _UNDER_BUDGET_ANCHORS) if lo > 0 else 1.0


def _interpolate(ratio: float, anchors: Tuple[Tuple[float, float], ...]) -> float:
    if ratio <= anchors[0][0]:
        return anchors[0][1]
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if ratio <= x1:
            fraction = (ratio - x0) / (x1 - x0)
            return y0 + fraction * (y1 - y0)
    return anchors[-1][1]


def _location_score(preferred_areas: Optional[str], property_area: Optional[str], property_address: Optional[str]) -> Optional[float]:
    if not preferred_areas or not (property_area or property_address):
        return None
    client_areas = [normalization.normalize_token(a) for a in preferred_areas.replace("/", ",").split(",")]
    haystack = normalization.normalize_token(f"{property_area or ''} {property_address or ''}")
    if any(area and area in haystack for area in client_areas):
        return 1.0
    # Not an exact area match, but both sides have location data — a real
    # but modest signal (the feature spec's "everything else matches but
    # area is different -> low score" case), not a hard exclusion.
    return 0.35


def _size_score(wanted: SizeRange, area_sqft: Optional[float]) -> Optional[float]:
    """1.0 inside the wanted range, easing down (never to zero) the further
    outside it the property is — see _SIZE_ANCHORS. None when the property
    has no usable area, so it is neither a match nor a miss."""
    if area_sqft is None:
        return None
    low, high = wanted
    if low is not None and area_sqft < low:
        return _interpolate((low - area_sqft) / low, _SIZE_ANCHORS)
    if high is not None and area_sqft > high:
        return _interpolate((area_sqft - high) / high, _SIZE_ANCHORS)
    return 1.0


def _semantic_score(client_vector: Optional[List[float]], property_vector: Optional[List[float]]) -> Optional[float]:
    # Length checks rather than truthiness: a builder project's vector is
    # held as a compact float32 numpy array (see Service/BuilderProjectService/
    # builder_project_store.py), and an array has no single truth value. For
    # the plain lists every property and client carries this is exactly the
    # old `not vector` test — None and [] are "not comparable", as before.
    if client_vector is None or property_vector is None or len(client_vector) == 0 or len(property_vector) == 0:
        return None
    similarity = float(np.dot(np.asarray(client_vector), np.asarray(property_vector)))
    return max(0.0, min(1.0, similarity))
