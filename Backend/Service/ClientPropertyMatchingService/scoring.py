"""Client-Property match scoring — one (client, property) pair in, one
MatchScore out.

THE CORE PRINCIPLE: TWO NUMBERS, NEVER ONE
-----------------------------------------------------------------------
This engine answers two different questions and refuses to average them
together:

  match_score       how well does this property match the requirements the
                    client ACTUALLY GAVE?
  confidence_score  how much do we actually KNOW — how complete is the
                    brief, and how much of it could this listing answer?

A client who said only "up to ₹1cr" and a ₹95L flat is a 92% match on
everything they told us, at low confidence. The old engine rewrote that 92
into a 76 so it would land in the Low bucket, which is the one thing a
broker must never be shown: a made-up number. Both numbers are now computed,
stored and displayed separately, and neither is ever used to bend the other.

WHAT HAPPENS, IN ORDER
-----------------------------------------------------------------------
STAGE 1 — ELIGIBILITY (is_eligible). Pass/fail, no arithmetic, and by far
the cheapest thing here: it runs before any vector is touched, so for a
client who named a type or a purpose the overwhelming majority of the
property list costs two dictionary lookups and nothing else. Someone who
asked to RENT is never shown something for sale; someone who asked for a
FLAT is never shown a bungalow or a plot, however perfectly the area, the
budget and the size line up; a listing far over a stated maximum is gone;
a BHK the client ruled out is gone. What eligibility may NEVER do is turn an
UNSTATED preference into a filter — a client who gave only a budget has
ruled nothing out, so nothing is ruled out on their behalf.

STAGE 2 — FIELD SCORES. Each requirement the CLIENT STATED is scored in
[0, 1] on its own: budget against a target band (a ceiling is a target, not
just a limit), location across real tiers of geographic relevance, BHK by
distance in bedrooms, property type by compatibility, size, furnishing,
purpose, and a low-weight whole-vector semantic check. A requirement the
client never stated is not scored and carries no weight. A requirement the
client DID state and this listing cannot answer is an UNKNOWN — priced as
one (config.UNKNOWN_FIELD_SCORE), never as a free pass. A listing with no
price on it is not a perfect budget match.

STAGE 3 — THE TWO SCORES. match_score is the weighted average of those
fields over the weight of what the CLIENT ASKED FOR — never over the weight
of what this property happens to have data for. confidence_score is a
separate sum over the same stated requirements, weighted by how much of a
brief each represents, with most of a requirement's weight withheld when
this listing could not answer it.

STAGE 4 — BUCKETS AND RANKING, both read straight off the real numbers.
Nothing is rescaled, clipped or nudged to land in a bucket.

SEMANTIC SIMILARITY IS A SUPPORTING SIGNAL ONLY
-----------------------------------------------------------------------
It is one low-weight field among several and it sits BEHIND the eligibility
gate, so it can never rescue an incompatible property: a beautifully worded
"3 BHK Bungalow" is still rejected for someone who asked for a Flat, and no
description similarity anywhere can change that.

COST
-----------------------------------------------------------------------
Everything about a client that does not change from property to property is
worked out once, in ClientBrief. Everything about a PROPERTY that does not
change from client to client is memoised by value (see normalization's
location cache and ClientBrief's per-value lookup tables). The explanation
strings are built only for pairs that are actually going to be kept. Only
properties that are matchable at all ever reach this (a property in the
review queue is filtered out upstream — matching_service._is_matchable).

Every tunable number lives in match_config.py, not here.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from Model.ClientPropertyMatchingModel.match_bucket import MatchBucket
from Model.ClientPropertyMatchingModel.match_score import (
    FIELD_ORDER as _FIELD_ORDER,
    MatchScore,
    matched_requirements,
    missing_information,
)
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty
from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord
from Service.ClientPropertyMatchingService import match_config as config
from Service.ClientPropertyMatchingService import normalization

# Re-exported so callers and tests can read the engine's thresholds off the
# engine, while the values themselves stay in one place (match_config).
HIGH_CUTOFF = config.MATCH_HIGH_CUTOFF
MEDIUM_CUTOFF = config.MATCH_MEDIUM_CUTOFF
LOW_CUTOFF = config.MIN_STORED_MATCH_SCORE
CONFIDENCE_HIGH_CUTOFF = config.CONFIDENCE_HIGH_CUTOFF
CONFIDENCE_MEDIUM_CUTOFF = config.CONFIDENCE_MEDIUM_CUTOFF
PARTIAL_EVIDENCE_CUTOFF = config.PARTIAL_EVIDENCE_CUTOFF
UNKNOWN_FIELD_SCORE = config.UNKNOWN_FIELD_SCORE
MAX_MATCHES_PER_CLIENT = config.MAX_MATCHES_PER_CLIENT

SizeRange = Tuple[Optional[float], Optional[float]]


class ClientBrief:
    """Everything about ONE client's requirements that does not change from
    property to property, worked out once and reused for all of them.

    This is a performance object, not a second source of truth: every value
    on it is the same helper the scoring functions would otherwise call
    inline. The difference is how often. Scoring a client against this
    project's property list used to re-parse that client's own BHK wording,
    re-split their own preferred areas and re-run their own property-type
    regex once PER PROPERTY — the identical answer, recomputed a couple of
    thousand times per client and several hundred thousand times per nightly
    run, for no purpose at all. The lookups keyed on the PROPERTY's own
    wording (`_type_gates`, `_bhk_scores`, `_purpose_gates`,
    `_furnishing_scores`) collapse the same work again: this database holds
    ten distinct property types and a few dozen distinct BHK strings between
    all of its listings, so those are computed once each and then read.

    Built by build_brief() below; scoring never constructs one itself.
    """

    __slots__ = (
        "purpose",
        "purpose_stated",
        "plan",
        "whole_type",
        "multi_type",
        "type_stated",
        "budget_min",
        "budget_max",
        "budget_lo",
        "budget_hi",
        "budget_hard_max",
        "budget_stated",
        "area_tokens",
        "bhk_stated",
        "bhk_raw",
        "bhk_max_distance",
        "furnishing_level",
        "vector",
        "_vector_np",
        "semantic_stated",
        "free_text_stated",
        "has_structured_requirement",
        "_confidence_weights",
        "_type_gates",
        "_bhk_scores",
        "_purpose_gates",
        "_furnishing_scores",
        "_group_tokens",
    )

    def __init__(self, client: ClientRecord, client_vector: Optional[List[float]]):
        self.purpose = client.purpose
        # Only "buy" and "rent" are real purposes. Anything else ("sell",
        # free text, empty) is not a stated requirement and never filters or
        # scores anything.
        self.purpose_stated = (client.purpose or "").strip().lower() in ("buy", "rent")

        self.plan = client_type_plan(client)
        # The original "one type and no size" path: the client's property_type
        # is read WHOLE, which is what a broker requirement's comma list and
        # free text like "flat preferred, but open to villa" mean. Several
        # types, or a size against one of them, is scored per type instead.
        self.whole_type = len(self.plan) <= 1 and not (self.plan and self.plan[0][1])
        # Only a client with a real CHOICE of types has a matched_type to
        # record; one type with a size against it is scored per type for the
        # size's sake alone, and stays untagged exactly as it always was.
        self.multi_type = len(self.plan) > 1
        # Whether the client named a kind of property at all. Everything
        # downstream keys off this: the gate only rejects on a stated
        # preference, and the type only counts as evidence when there is one.
        self.type_stated = bool(normalization.split_client_property_types(client.property_type))

        self.budget_min = client.budget_min_inr
        self.budget_max = client.budget_max_inr
        self.budget_stated = self.budget_min is not None or self.budget_max is not None
        self.budget_lo, self.budget_hi = _budget_bounds(self.budget_min, self.budget_max)
        self.budget_hard_max = (
            self.budget_max * (1.0 + config.BUDGET_OVER_TOLERANCE) if self.budget_max else None
        )

        self.area_tokens = normalization.client_area_tokens(client.preferred_areas)

        self.bhk_raw = client.bhk
        self.bhk_stated = normalization.parse_bhk_intent(client.bhk) is not None
        # "exactly 3 BHK" rules everything else out; a plain "3 BHK" accepts a
        # neighbour. Resolved once here so the gate is one comparison.
        self.bhk_max_distance = (
            config.BHK_STRICT_MAX_DISTANCE
            if normalization.bhk_is_strict(client.bhk)
            else config.BHK_MAX_DISTANCE
        )

        self.furnishing_level = normalization.furnishing_level(client.furnishing)

        self.vector = client_vector or []
        self._vector_np = np.asarray(self.vector, dtype=np.float32) if self.vector else None
        self.semantic_stated = self._vector_np is not None
        # Confidence counts the semantic field only when the client actually
        # WROTE something free-form. Every client with any requirement has a
        # vector (it is built from their structured fields too), so treating
        # the vector's mere existence as evidence would hand every client a
        # confidence bonus for nothing.
        self.free_text_stated = bool((client.additional_requirements or "").strip())

        # Whether the client stated ANY requirement that can actually be
        # compared field to field. If they did not, nothing is matched at all:
        # semantic similarity is a supporting signal and must never be the
        # whole of a score (§12). Without this, a client whose only stored
        # requirement is the word "investment" would be handed a shortlist
        # ranked purely on embedding resemblance — which is the noise this
        # engine exists to keep out of the broker's way. One such client
        # exists in this project's own data.
        self.has_structured_requirement = bool(
            self.budget_stated
            or self.area_tokens
            or self.type_stated
            or self.bhk_stated
            or self.purpose_stated
            or self.furnishing_level is not None
            or any(size for _, size in self.plan)
        )

        self._confidence_weights: Dict[str, float] = dict(config.CONFIDENCE_WEIGHTS)
        if not self.free_text_stated:
            self._confidence_weights["semantic"] = 0.0

        self._type_gates: Dict[str, List[float]] = {}
        self._bhk_scores: Dict[str, Optional[float]] = {}
        self._purpose_gates: Dict[str, float] = {}
        self._furnishing_scores: Dict[str, Optional[float]] = {}
        self._group_tokens: Optional[List[List[str]]] = None

    # -- per-property lookups, each answered once per distinct value --------

    def type_gates(self, property_type: Optional[str]) -> List[float]:
        """This property's type-compatibility factor against each of the
        client's type groups (one entry when the whole value is read as a
        single preference)."""
        key = property_type or ""
        gates = self._type_gates.get(key)
        if gates is None:
            if self.whole_type:
                raw = self.plan[0][0] if self.plan else None
                gates = [normalization.property_type_gate(raw, property_type)]
            else:
                gates = [normalization.property_type_gate(group, property_type) for group, _ in self.plan]
            self._type_gates[key] = gates
        return gates

    def bhk_score(self, property_bhk: Optional[str]) -> Optional[float]:
        key = property_bhk or ""
        if key not in self._bhk_scores:
            self._bhk_scores[key] = normalization.bhk_score(self.bhk_raw, property_bhk)
        return self._bhk_scores[key]

    def bhk_distance(self, property_bhk: Optional[str]) -> Optional[float]:
        """The eligibility question, answered off the same memo as the score:
        a distance of None means "not comparable", never "too far"."""
        score = self.bhk_score(property_bhk)
        if score is None:
            return None
        return normalization.bhk_distance(self.bhk_raw, property_bhk)

    def purpose_gate(self, listing_type: Optional[str]) -> float:
        key = listing_type or ""
        if key not in self._purpose_gates:
            self._purpose_gates[key] = _purpose_gate(self.purpose, listing_type)
        return self._purpose_gates[key]

    def furnishing_score(self, property_furnishing: Optional[str]) -> Optional[float]:
        if self.furnishing_level is None:
            return None
        key = property_furnishing or ""
        if key not in self._furnishing_scores:
            offered = normalization.furnishing_level(property_furnishing)
            self._furnishing_scores[key] = (
                None if offered is None else normalization.furnishing_distance_score(self.furnishing_level, offered)
            )
        return self._furnishing_scores[key]

    def confidence_weight(self, field: str) -> float:
        """How much of a complete brief stating this requirement represents —
        0.0 for a field that is not evidence about what the client wants (see
        match_config.CONFIDENCE_WEIGHTS and the note on purpose)."""
        return self._confidence_weights.get(field, 0.0)

    def group_tokens(self, index: int) -> List[str]:
        """The type tokens of one of the client's groups — used only for the
        tie-break between two groups that scored the same."""
        if self._group_tokens is None:
            self._group_tokens = [normalization.split_client_property_types(group) for group, _ in self.plan]
        return self._group_tokens[index]


def build_brief(client: ClientRecord, client_vector: Optional[List[float]] = None) -> ClientBrief:
    """The per-client half of scoring, worked out once. Hand the result to
    score_client_property for every property — see ClientBrief."""
    return ClientBrief(client, client_vector)


def client_type_plan(client: ClientRecord) -> List[Tuple[str, Optional[SizeRange]]]:
    """Each property type the client picked, with the size range they gave
    for it (None when they gave none, or nothing readable). Worked out ONCE
    per client (by build_brief) and reused for every property, rather than
    re-parsing the same text thousands of times per recompute."""
    groups = [
        (group, normalization.parse_size_requirement(normalization.size_for(client.property_sizes, group), group))
        for group in normalization.split_type_groups(client.property_type)
    ]
    # No type at all is still one "group" — the empty preference, which
    # property_type_gate reads as "every type is open". Keeping it here means
    # the whole-value path below always has something to read.
    return groups if groups else [(client.property_type or "", None)]


# =========================================================================
# STAGE 1 — ELIGIBILITY
# =========================================================================


def is_eligible(prop: EmbeddedProperty, brief: ClientBrief) -> bool:
    """Whether this pair is allowed to be a match at all.

    Every rule here is pass/fail and every rule here is about something the
    CLIENT EXPLICITLY STATED. Cheap on purpose: it is the first thing scoring
    does, and for a client who named a type or a purpose it rejects most of
    the property list on two dictionary lookups, before a single vector is
    touched or a single string is parsed.

    Public so a caller that wants to know "would this property match?"
    without paying for a full score can ask the same question the engine
    itself asks, rather than a second copy of it that could drift.
    """
    # Purpose — buy vs rent. A rental seeker is never shown something for
    # sale. A listing that does not say which it is passes (an unknown, not a
    # contradiction).
    if brief.purpose_gate(prop.listing_type) < config.PURPOSE_HARD_FLOOR:
        return False

    # Property type — exact and clearly compatible alternatives pass, a
    # different category does not. Only when the client named a type.
    if brief.type_stated:
        gate = max(brief.type_gates(prop.property_type))
        if gate < config.TYPE_HARD_FLOOR:
            return False
        if not prop.property_type and config.REJECT_UNKNOWN_PROPERTY_TYPE:
            # The client named a kind of property and this listing does not
            # say what it is. Nothing here can be compared, and a listing
            # with no type on it is one nobody can act on — so it is
            # rejected rather than guessed at in either direction.
            return False

    # Budget — a hard filter in ONE direction only. Clearly too expensive is
    # out; cheaper is never rejected for being cheap (it loses score on the
    # under-budget curve instead).
    if (
        brief.budget_hard_max is not None
        and prop.price_amount_inr is not None
        and prop.price_amount_inr > brief.budget_hard_max
    ):
        return False

    # BHK — in bedrooms, not in score. A property may never buy its way past
    # an unacceptable number of bedrooms with price or location.
    if brief.bhk_stated:
        distance = brief.bhk_distance(prop.bhk)
        if distance is not None and distance > brief.bhk_max_distance:
            return False
        # ...and a bedroom count means a home: somebody who asked for a BHK
        # and named no type is not shown plots or shops. See
        # config.REJECT_NON_RESIDENTIAL_FOR_BHK.
        if (
            config.REJECT_NON_RESIDENTIAL_FOR_BHK
            and not brief.type_stated
            and normalization.is_non_residential_type(prop.property_type)
        ):
            return False

    return True


# =========================================================================
# STAGE 2/3 — FIELD SCORES, THEN THE TWO SCORES
# =========================================================================


def score_property(client: ClientRecord, prop: EmbeddedProperty, client_vector: List[float]) -> Optional[MatchScore]:
    """One pair, scored from scratch — builds the client's brief and throws
    it away, so it is for one-off callers only. Anything scoring a client
    against a list builds the brief once (build_brief) and calls
    score_client_property with it."""
    return score_client_property(prop, build_brief(client, client_vector))


def score_client_property(prop: EmbeddedProperty, brief: ClientBrief) -> Optional[MatchScore]:
    """Returns None (not a MatchScore) when the pair is ineligible (see
    is_eligible) or when the match score falls below
    config.MIN_STORED_MATCH_SCORE — the caller (matching_service.py) stores
    only what comes back, so neither ever reaches the cache or the dashboard,
    in any bucket. A thin brief never removes anything here: that is what
    confidence is for.

    A client may have picked several property types ("Flat, Bungalow") and a
    size for each. Every type is an equal preference, so the property is
    scored against each one on its own — that type's gate and that type's
    size, everything else shared — and keeps its best result, tagged with
    the type it was for (`matched_type`, only when there is more than one to
    choose between). A tie goes to the type naming this property exactly,
    then to the one picked first."""
    # Nothing comparable was stated, so no claim about this pair can be
    # supported — see ClientBrief.has_structured_requirement. Checked first
    # because it is one attribute read and it removes the whole property list.
    if not brief.has_structured_requirement:
        return None
    if not is_eligible(prop, brief):
        return None

    # The fields that are the same whichever of the client's types this is
    # scored under — computed once per pair, not once per type group.
    shared: Dict[str, Optional[float]] = {}
    if brief.budget_stated:
        shared["budget"] = _budget_score(brief.budget_lo, brief.budget_hi, prop.price_amount_inr)
    if brief.area_tokens:
        shared["location"] = normalization.location_score(
            brief.area_tokens, prop.area_name, prop.address, prop.society_name
        )
    if brief.bhk_stated:
        shared["bhk"] = brief.bhk_score(prop.bhk)
    if brief.furnishing_level is not None:
        shared["furnishing"] = brief.furnishing_score(prop.furnishing)
    if brief.purpose_stated:
        gate = brief.purpose_gate(prop.listing_type)
        # Past the gate, purpose is either right (1.0) or unstated on the
        # listing (an unknown) — a contradiction never gets here.
        shared["purpose"] = 1.0 if gate >= 1.0 else None
    if brief.semantic_stated:
        shared["semantic"] = _semantic_score(brief._vector_np, prop.embedding)

    type_gates = brief.type_gates(prop.property_type)

    if brief.whole_type:
        fields = shared
        if brief.type_stated:
            fields["property_type"] = _type_field_score(type_gates[0], prop.property_type)
        return _finish(prop, fields, brief, None)

    prop_token = normalization.canonical_type_token(prop.property_type) if prop.property_type else ""
    area_sqft = normalization.property_area_sqft(prop.area_sqft, prop.area_vaar)

    best: Optional[MatchScore] = None
    best_key: Optional[tuple] = None
    for index, (group, wanted) in enumerate(brief.plan):
        gate = type_gates[index]
        if brief.type_stated and gate < config.TYPE_HARD_FLOOR:
            # This particular type of the client's is incompatible with the
            # listing. Another of their types may still be compatible (that
            # is what got the pair past eligibility), but this one is not, and
            # a property must never be scored under a type it contradicts.
            continue
        fields = dict(shared)
        if brief.type_stated:
            fields["property_type"] = _type_field_score(gate, prop.property_type)
        if wanted is not None:
            # The client gave a size for this type. A listing with no usable
            # area is an UNKNOWN on it — never a perfect size match.
            fields["size"] = _size_score(wanted, area_sqft)
        score = _finish(prop, fields, brief, group if brief.multi_type else None)
        if score is None:
            continue
        key = (score.score, prop_token in brief.group_tokens(index), -index)
        if best_key is None or key > best_key:
            best, best_key = score, key
    return best


def _finish(
    prop: EmbeddedProperty,
    fields: Dict[str, Optional[float]],
    brief: ClientBrief,
    matched_type: Optional[str],
) -> Optional[MatchScore]:
    """The two scores, the two buckets, and — only for a pair that is
    actually going to be kept — its explanation.

    `fields` holds exactly the requirements the CLIENT stated: a value in
    [0, 1] where this listing could answer, None where it could not. That is
    the whole contract, and the reason the denominator can never drift onto
    "whatever this property happens to have data for"."""
    numerator = 0.0
    weight_total = 0.0
    answered_weight = 0.0
    confidence = 0.0
    # The worst KNOWN score among the client's core requirements — what caps
    # the total (see config.CORE_MISS_CEILINGS). Starts at 1.0 so a client who
    # stated no core requirement, or a listing that could answer none of them,
    # is never capped for it.
    worst_core = 1.0

    for name, value in fields.items():
        weight = config.MATCH_WEIGHTS[name]
        weight_total += weight
        confidence_weight = brief.confidence_weight(name)
        if value is not None and name in config.CORE_REQUIREMENTS and value < worst_core:
            worst_core = value
        if value is None:
            # Stated by the client, unanswerable from this listing. Priced as
            # an uncertainty in the score, and most of its confidence
            # withheld — never dropped, which would silently re-weight
            # everything else up to 100%.
            numerator += weight * config.UNKNOWN_FIELD_SCORE
            confidence += confidence_weight * config.UNANSWERED_CONFIDENCE_CREDIT
        else:
            numerator += weight * value
            answered_weight += weight
            confidence += confidence_weight

    if not weight_total:
        # The client stated no requirement this engine can compare at all, so
        # there is nothing to have matched. Not a 50% match, not a Low match —
        # no match, because no claim about this pair can be supported.
        # (matching_service never gets here: a client with no requirements is
        # not scored. This is the guard that keeps that true if it ever does.)
        return None

    # A requirement the client stated and this property does not meet holds
    # the whole match down, however well the rest of the brief went — no
    # amount of budget or location may pay for the wrong number of bedrooms.
    # See config.CORE_MISS_CEILINGS, including why this is not the rescaling
    # the specification forbids.
    match_score = min(
        numerator / weight_total,
        _interpolate(worst_core, config.CORE_MISS_CEILINGS),
    )
    if match_score < config.MIN_STORED_MATCH_SCORE:
        return None

    confidence_score = min(1.0, confidence)
    evidence_ratio = answered_weight / weight_total

    return MatchScore(
        record_id=prop.record_id,
        score=round(match_score, 4),
        bucket=match_bucket(match_score),
        confidence_score=round(confidence_score, 4),
        evidence_ratio=round(evidence_ratio, 4),
        is_partial_match=evidence_ratio < config.PARTIAL_EVIDENCE_CUTOFF,
        property_category=_category_of(prop),
        # Exactly the client's stated requirements, and nothing else. Both
        # derived lists below and the two lists the dashboard shows are read
        # back out of this, so they can never disagree with the score.
        field_scores={name: fields[name] for name in _FIELD_ORDER if name in fields},
        reason="; ".join(_reasons(fields, prop)),
        matched_type=matched_type,
    )


def match_bucket(score: float) -> MatchBucket:
    """Read straight off the real score — nothing is rescaled to land here."""
    if score >= config.MATCH_HIGH_CUTOFF:
        return MatchBucket.HIGH
    if score >= config.MATCH_MEDIUM_CUTOFF:
        return MatchBucket.MEDIUM
    return MatchBucket.LOW


# The confidence half of the bucketing lives on the model that carries the
# score (MatchScore.confidence_bucket), because it is a pure function of
# confidence_score and is therefore never stored — see that property.


# =========================================================================
# STAGE 4 — RANKING
# =========================================================================


def ranking_key(match: MatchScore) -> tuple:
    """How a client's shortlist is ordered, and how the top
    MAX_MATCHES_PER_CLIENT are chosen.

      1. the match score — how well it fits what was asked for;
      2. confidence, as the tie-break;
      3. how many stated requirements it hits EXACTLY;
      4. how much of the brief the listing could answer at all.

    Note the order. A property with a genuinely better match score is never
    ranked below a worse one merely because more is known about it — extra
    information breaks ties, it does not win them.

    Derived entirely from the stored row (no extra column), so a cached match
    ranks the same way it did when it was computed."""
    exact = 0
    for value in match.field_scores.values():
        if value is not None and value >= 0.999:
            exact += 1
    return (match.score, match.confidence_score, exact, match.evidence_ratio)


# =========================================================================
# EXPLANATION
#
# `matched_requirements` and `missing_information` are re-exported from the
# model that owns the field vocabulary (Model/ClientPropertyMatchingModel/
# match_score.py) — they are derived from field_scores, not stored, so the
# engine and the dashboard read the same two functions.
# =========================================================================


def _reasons(fields: Dict[str, Optional[float]], prop: EmbeddedProperty) -> List[str]:
    """One short, plain sentence per stated requirement, in a fixed order.

    Deliberately short: this is the text a broker reads on a card, and it is
    also stored on every match row, so every extra clause is bytes in Neon
    and bytes over the wire multiplied by the size of the shortlist. Never
    contains a semicolon — the stored form is these joined by "; " and split
    back apart on read."""
    notes: List[str] = []
    for name in _FIELD_ORDER:
        if name not in fields:
            continue
        note = _REASON_TEXT[name](fields[name], prop)
        if note:
            notes.append(note)
    return notes or ["Matches on the requirements that were compared."]


def _budget_reason(value: Optional[float], prop: EmbeddedProperty) -> Optional[str]:
    if value is None:
        return "Listing does not say what it costs"
    if value >= 0.999:
        return "Within the requested budget"
    if value >= 0.7:
        return "Just outside the requested budget"
    if value >= 0.4:
        return "Outside the requested budget"
    return "Well outside the requested budget"


def _location_reason(value: Optional[float], prop: EmbeddedProperty) -> Optional[str]:
    if value is None:
        return "Listing does not say where it is"
    if value >= config.LOCATION_EXACT:
        return "In a requested area"
    if value >= config.LOCATION_FUZZY:
        return "In or beside a requested area"
    if value >= config.LOCATION_NEARBY:
        return "In a neighbouring area"
    if value >= config.LOCATION_SAME_CITY:
        return "Same city, a different area"
    return "A different area from the ones requested"


def _bhk_reason(value: Optional[float], prop: EmbeddedProperty) -> Optional[str]:
    if value is None:
        return "Listing does not say how many bedrooms"
    if value >= 0.999:
        return "Exactly the requested BHK"
    return "A different BHK from the one requested"


def _type_reason(value: Optional[float], prop: EmbeddedProperty) -> Optional[str]:
    if value is None:
        return "Listing does not say what kind of property it is"
    if value >= 0.999:
        return "Exactly the requested property type"
    if value >= 0.8:
        return "A compatible property type"
    return "A loosely compatible property type"


def _size_reason(value: Optional[float], prop: EmbeddedProperty) -> Optional[str]:
    if value is None:
        return "Listing does not say its size"
    if value >= 0.999:
        return "Size is in the requested range"
    if value >= 0.6:
        return "Size is close to the requested range"
    return "Size is outside the requested range"


def _furnishing_reason(value: Optional[float], prop: EmbeddedProperty) -> Optional[str]:
    if value is None:
        return "Listing does not say how furnished it is"
    if value >= 0.999:
        return "Furnishing is as requested"
    if value >= 0.5:
        return "Furnishing is one level away"
    return "Furnishing is not what was asked for"


def _purpose_reason(value: Optional[float], prop: EmbeddedProperty) -> Optional[str]:
    if value is None:
        return "Listing does not say whether it is for sale or to rent"
    # Said plainly, because "right for buying" is the whole of what passing
    # this gate means and a broker should see it confirmed.
    return "For sale, as requested" if prop.listing_type == "Sale" else "To rent, as requested"


def _semantic_reason(value: Optional[float], prop: EmbeddedProperty) -> Optional[str]:
    # Mentioned only when it actually says something. A middling similarity
    # is noise, and printing it on every card would cost bytes on every row
    # to tell the reader nothing.
    if value is None:
        return None
    if value >= 0.75:
        return "Description closely matches the brief"
    if value <= 0.3:
        return "Description has little in common with the brief"
    return None


_REASON_TEXT = {
    "budget": _budget_reason,
    "location": _location_reason,
    "bhk": _bhk_reason,
    "property_type": _type_reason,
    "size": _size_reason,
    "furnishing": _furnishing_reason,
    "purpose": _purpose_reason,
    "semantic": _semantic_reason,
}


def _category_of(prop: EmbeddedProperty) -> str:
    if prop.needs_review:
        return "needs_review"
    return "main" if prop.review_status == "accepted" else "outsider"


# =========================================================================
# INDIVIDUAL FIELD SCORES
# =========================================================================


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


def _type_field_score(gate: float, property_type: Optional[str]) -> Optional[float]:
    """Property type as a RANKING field, once compatibility has already been
    decided by the gate. None when the listing does not say what it is (an
    unknown) — which, with config.REJECT_UNKNOWN_PROPERTY_TYPE on, only a
    client who stated no type ever reaches.

    Semantic similarity can never touch this: an incompatible type is gone
    before any of this runs."""
    if not property_type:
        return None
    return gate


def _budget_bounds(budget_min: Optional[float], budget_max: Optional[float]) -> Tuple[float, float]:
    """The range a property's price is scored against.

    A client who gave both ends is used exactly as written. A client who gave
    only a ceiling gets a target BAND below it (config.BUDGET_TARGET_BAND)
    rather than a floor of zero: "up to ₹1cr" is a target, not merely a
    limit, and a ₹20L listing is a different kind of property rather than a
    bargain."""
    hi = budget_max if budget_max is not None else float("inf")
    if budget_min is not None:
        return budget_min, hi
    if budget_max is not None:
        return budget_max * config.BUDGET_TARGET_BAND, hi
    return 0.0, hi


def _budget_score(lo: float, hi: float, price: Optional[float]) -> Optional[float]:
    """1.0 inside the target range, easing down outside it — and None when
    the listing has NO PRICE. That None is the single most important return
    value in this module: it becomes an UNKNOWN, not a perfect match."""
    if price is None:
        return None
    if lo <= price <= hi:
        return 1.0
    if price > hi:
        return _interpolate((price - hi) / hi, config.BUDGET_OVER_ANCHORS) if hi > 0 else 1.0
    return _interpolate((lo - price) / lo, config.BUDGET_UNDER_ANCHORS) if lo > 0 else 1.0


def _size_score(wanted: SizeRange, area_sqft: Optional[float]) -> Optional[float]:
    """1.0 inside the wanted range, easing down (never to zero) the further
    outside it the property is. None when the listing has no usable area —
    an UNKNOWN, for the same reason a missing price is one."""
    if area_sqft is None:
        return None
    low, high = wanted
    if low is not None and area_sqft < low:
        return _interpolate((low - area_sqft) / low, config.SIZE_ANCHORS)
    if high is not None and area_sqft > high:
        return _interpolate((area_sqft - high) / high, config.SIZE_ANCHORS)
    return 1.0


def _interpolate(ratio: float, anchors: Tuple[Tuple[float, float], ...]) -> float:
    if ratio <= anchors[0][0]:
        return anchors[0][1]
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if ratio <= x1:
            fraction = (ratio - x0) / (x1 - x0)
            return y0 + fraction * (y1 - y0)
    return anchors[-1][1]


def _semantic_score(client_vector, property_vector) -> Optional[float]:
    """Cosine similarity of the two stored, already-normalized embeddings —
    a SUPPORTING signal on config.MATCH_WEIGHTS["semantic"] weight, behind
    the eligibility gate, and never able to override a structured
    requirement.

    Length checks rather than truthiness: a builder project's vector is held
    as a compact float32 numpy array (see Service/BuilderProjectService/
    builder_project_store.py), and an array has no single truth value. For
    the plain lists every property carries this is exactly the old
    `not vector` test — None and [] are "not comparable", as before."""
    if client_vector is None or property_vector is None or len(property_vector) == 0:
        return None
    similarity = float(np.dot(client_vector, np.asarray(property_vector, dtype=np.float32)))
    return max(0.0, min(1.0, similarity))
