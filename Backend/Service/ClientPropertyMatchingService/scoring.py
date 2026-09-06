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
budget, location, BHK, and a whole-vector semantic sanity check — mirroring
Service/WhatsAppDataFetchingService/duplicate_detection_service.py's
_score_candidate: any field that isn't comparable on both sides scores
None and is excluded from both the numerator and the weight total, never
treated as a match or a mismatch. `evidence_ratio` (how much of the total
weight was actually backed by data) drives the `is_partial_match` flag the
dashboard shows alongside the bucket.
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
MEDIUM_CUTOFF = 0.85
# Below this, a property isn't shown as a match at all (score_property
# returns None) — not even as "Low". Per the feature owner's explicit
# requirement: only 80-100% should ever reach the dashboard.
LOW_CUTOFF = 0.80
PARTIAL_EVIDENCE_CUTOFF = 0.6

# Calibration points for graduated budget proximity — (ratio-away-from-the
# nearest bound, score). Piecewise-linear between them, same technique as
# duplicate_detection_service.py's _numeric_field_score but with more
# anchor points for a smoother curve matching the feature spec's own
# worked examples (₹89L/90L max -> ~1.0, ₹92L -> ~0.88, ₹96L -> ~somewhat
# outside, ₹1.10cr -> poor fit, ₹1.5cr -> very poor fit).
_OVER_BUDGET_ANCHORS: Tuple[Tuple[float, float], ...] = ((0.0, 1.0), (0.05, 0.85), (0.15, 0.55), (0.30, 0.25), (0.60, 0.05))
_UNDER_BUDGET_ANCHORS: Tuple[Tuple[float, float], ...] = ((0.0, 1.0), (0.15, 0.85), (0.40, 0.65), (1.0, 0.5))


def score_property(client: ClientRecord, prop: EmbeddedProperty, client_vector: List[float]) -> Optional[MatchScore]:
    """Returns None (not a MatchScore) when the final score falls below
    LOW_CUTOFF — the caller (matching_service.py) filters these out, so a
    property scoring under 80% never reaches the dashboard at all, in any
    bucket."""
    purpose_factor = _purpose_gate(client.purpose, prop.listing_type)
    type_factor = normalization.property_type_gate(client.property_type, prop.property_type)
    critical_gate = purpose_factor * type_factor

    field_scores: Dict[str, Optional[float]] = {
        "budget": _budget_score(client.budget_min_inr, client.budget_max_inr, prop.price_amount_inr),
        "location": _location_score(client.preferred_areas, prop.area_name, prop.address),
        "bhk": normalization.bhk_score(client.bhk, prop.bhk),
        "semantic": _semantic_score(client_vector, prop.embedding),
    }

    comparable_weight = sum(_SOFT_WEIGHTS[name] for name, score in field_scores.items() if score is not None)
    total_weight = sum(_SOFT_WEIGHTS.values())
    evidence_ratio = comparable_weight / total_weight if total_weight else 0.0

    if comparable_weight == 0:
        soft_score = 0.5  # nothing comparable at all — neutral, not zero
    else:
        soft_score = (
            sum(_SOFT_WEIGHTS[name] * score for name, score in field_scores.items() if score is not None)
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
        reason=_build_reason(purpose_factor, type_factor, evidence_ratio),
    )


def _category_of(prop: EmbeddedProperty) -> str:
    if prop.needs_review:
        return "needs_review"
    return "main" if prop.review_status == "accepted" else "outsider"


def _build_reason(purpose_factor: float, type_factor: float, evidence_ratio: float) -> str:
    notes: List[str] = []
    if purpose_factor < 0.5:
        notes.append("purpose (buy/rent) does not match")
    if type_factor < 0.3:
        notes.append("property type is a poor fit")
    elif type_factor < 0.9:
        notes.append("property type is a partial fit")
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


def _semantic_score(client_vector: Optional[List[float]], property_vector: Optional[List[float]]) -> Optional[float]:
    if not client_vector or not property_vector:
        return None
    similarity = float(np.dot(np.array(client_vector), np.array(property_vector)))
    return max(0.0, min(1.0, similarity))
