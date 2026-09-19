"""Every tunable number the Client-Property matching engine uses, in one
place.

Why a module of its own: the engine answers two different questions (how
well does this property fit what the client ASKED FOR, and how much did the
client actually TELL US), and both are built out of constants that a broker
will want to calibrate against real results — over-budget tolerance, how far
off a BHK may be, how much a location miss costs, where High/Medium/Low sit.
Scattered through the scoring code those are invisible; gathered here they
are a settings sheet, and scoring.py reads them by name rather than owning
them.

Nothing here has behaviour. Import it as `config` and read the names.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Mapping, Tuple

# =========================================================================
# STAGE 1 — HARD ELIGIBILITY
#
# Pass/fail rules, applied only against requirements the CLIENT ACTUALLY
# STATED. An unstated requirement is never a filter: a client who gave only
# a budget has ruled nothing out, so nothing is ruled out on their behalf.
# =========================================================================

# Purpose (buy/rent). The gate scores 1.0 for the right listing type or a
# client who never said, 0.5 when the LISTING does not say which it is, and
# 0.08 for a genuine contradiction. At this floor a rental seeker is never
# shown something for sale, and a listing of unknown purpose still passes
# (it is an unknown, not a contradiction — see UNKNOWN_FIELD_SCORE).
PURPOSE_HARD_FLOOR = 0.5

# Property type. Exact (1.0), a sibling inside the same family (1.0/0.85 —
# apartment for someone who asked for a flat, bungalow for a row house), a
# type the client explicitly said they would also consider (0.8) and a near
# spelling of the same word (0.6) all pass. What does not: the 0.55 a
# bungalow scores for someone who asked for a FLAT, and the 0.15/0.08 of a
# plot or a shop against a home buyer.
TYPE_HARD_FLOOR = 0.6

# What to do when the client named a type and the LISTING does not say what
# it is (property_type_gate scores that 0.5). True rejects the pair rather
# than guessing; it is what the engine has always done, and a listing with
# no type on it is one nobody can act on anyway. Flip to False to let such a
# listing through as an UNKNOWN on the property-type field instead.
REJECT_UNKNOWN_PROPERTY_TYPE = True

# BHK, in bedrooms away from the nearest configuration the client would
# accept (see normalization.bhk_distance — 0 means the requirement is
# satisfied outright, including every value inside a stated range).
#
#   "3 BHK"          -> a 2 or a 4 stays eligible, a 1 or a 5 does not.
#   "exactly 3 BHK"  -> only a 3.
#
# A property may NEVER buy its way past this with price or location: an
# unacceptable number of bedrooms is a different property, not a discount.
BHK_MAX_DISTANCE = 1.0
BHK_STRICT_MAX_DISTANCE = 0.0

# How far over a stated MAXIMUM budget a property may still be eligible.
# 0.25 = a ₹1cr ceiling still considers ₹1.25cr, and rejects ₹1.4cr.
#
# Deliberately one-sided: a cheaper property is never rejected for being
# cheap (it simply scores lower on the budget curve the further below the
# client's target band it sits — see BUDGET_TARGET_BAND).
BUDGET_OVER_TOLERANCE = 0.25


# =========================================================================
# STAGE 2 — MATCH SCORE
#
# How well the property matches the requirements the client GAVE. The
# denominator is the weight of what the CLIENT asked for — never the weight
# of what this property happens to have data for. A field the client never
# stated is not scored and carries no weight at all.
# =========================================================================

MATCH_WEIGHTS: Dict[str, float] = {
    "budget": 0.40,
    "location": 0.30,
    "bhk": 0.20,
    "semantic": 0.10,
    # Property type is primarily a HARD compatibility check (TYPE_HARD_FLOOR
    # above) — anything incompatible is gone before scoring starts. This
    # small weight only separates an exact type from a compatible one when
    # ranking two properties that both passed.
    "property_type": 0.08,
    # Same: purpose has already passed its gate, so this only distinguishes
    # "the listing says Sale and the client wants to buy" from "the listing
    # does not say".
    "purpose": 0.05,
    # The two optional requirements. Low weight on purpose: most people only
    # have a rough idea of the size they want, and furnishing is the easiest
    # thing about a property to change — a miss on either should nudge a
    # property down the list, never knock a good one out of it.
    "size": 0.08,
    "furnishing": 0.06,
}

# What a field the client DID ask about, and this property cannot answer,
# scores. Neither a match nor a miss — an unknown, priced as one.
#
# The alternative (dropping the field from the average) quietly re-weights
# everything else up to 100% and lets a listing with no price on it count as
# a PERFECT budget match. That is the single most dangerous thing a matching
# engine can tell a broker, and this constant is what stops it.
UNKNOWN_FIELD_SCORE = 0.5

# Where the target band starts when a client gives a ceiling and no floor —
# the overwhelmingly common case on both the requirements form and the
# WhatsApp extraction. "Up to ₹1cr" does not mean "anything under ₹1cr":
# someone shopping there is looking in roughly ₹60L–₹1cr, and a ₹20L listing
# is a different kind of property, not a bargain. A SOFT floor: below it a
# property is still scored and can still match, it just stops outranking the
# ones the client is actually shopping for. A client who gave their own
# minimum never reaches this.
BUDGET_TARGET_BAND = 0.60

# The budget curve, as (how far outside the band, as a fraction of the
# nearest bound -> score) anchor points, linearly interpolated between.
# Inside the band is 1.0.
BUDGET_OVER_ANCHORS: Tuple[Tuple[float, float], ...] = (
    (0.0, 1.0),
    (0.05, 0.85),
    (0.15, 0.55),
    (0.30, 0.25),
    (0.60, 0.05),
)
BUDGET_UNDER_ANCHORS: Tuple[Tuple[float, float], ...] = (
    (0.0, 1.0),
    (0.15, 0.85),
    (0.40, 0.65),
    (1.0, 0.5),
)

# --- a missed core requirement puts a ceiling on the whole match ----------
#
# READ THIS BEFORE CHANGING IT. It is the difference between a useful engine
# and a misleading one.
#
# A weighted average lets a big field quietly pay for a small one. Measured
# against this project's real data, a client who asked for a 4 BHK Flat in
# Green City under ₹90L was being shown 3 BHK flats at "88% — High match,
# high confidence, nothing missing", because a perfect budget, a perfect area
# and a perfect property type between them outweighed the one field that was
# wrong. The bedroom count is the least negotiable thing about a home. That
# card was a lie told with correct arithmetic.
#
# So a match is also capped by how badly its WORST core requirement did. The
# anchors below map that worst score to the highest total the match may claim:
# a core requirement met exactly imposes no ceiling at all, one that is merely
# close barely lowers it, and one that is genuinely missed holds the match out
# of High however well everything else went.
#
# THIS IS NOT THE RESCALING §15 FORBIDS, and the distinction is the whole
# point of this engine. What is forbidden is lowering a score because the
# CLIENT told us little — punishing a property for someone else's silence.
# This lowers a score because a requirement the client DID state is NOT MET,
# which is precisely what a match score is supposed to measure. It is also
# §6's own instruction ("do not let budget or location compensate for an
# unacceptable BHK") applied to every core field rather than to BHK alone.
#
# An UNKNOWN (the listing cannot answer) deliberately does NOT impose a
# ceiling: unknown is not the same as wrong, and UNKNOWN_FIELD_SCORE already
# prices it inside the average. Keeping the two apart is what lets a broker
# tell "this is not what you asked for" from "we don't know yet".
CORE_REQUIREMENTS = ("budget", "location", "bhk", "property_type")
CORE_MISS_CEILINGS: Tuple[Tuple[float, float], ...] = (
    (0.00, 0.55),
    (0.30, 0.70),
    (0.50, 0.82),  # one BHK out, or well over budget -> Medium at best
    (0.85, 0.95),  # a compatible type, a neighbouring area -> High still open
    (1.00, 1.00),  # met exactly -> no ceiling
)

# The size curve, same shape. Gentler than the budget's, and never reaching
# zero, for the reason given on MATCH_WEIGHTS["size"].
SIZE_ANCHORS: Tuple[Tuple[float, float], ...] = (
    (0.0, 1.0),
    (0.10, 0.85),
    (0.25, 0.6),
    (0.50, 0.35),
    (1.0, 0.2),
)

# --- location tiers ------------------------------------------------------
#
# Location is NOT a yes/no check. These are the levels of geographic
# relevance, best first. Everything above LOCATION_SAME_CITY is decided from
# the words on both sides — no geography is invented anywhere.
LOCATION_EXACT = 1.0  # the client's area appears in the listing's own location
LOCATION_PARTIAL = 0.85  # a distinctive word of it does ("Vesu" vs "Vesu Road")
LOCATION_FUZZY = 0.80  # a near-spelling of it does ("Adajan" vs "Adajann")
LOCATION_NEARBY = 0.70  # a locality configured as adjacent (see NEARBY_AREAS)
LOCATION_SAME_CITY = 0.45  # same city, different locality
LOCATION_OTHER = 0.30  # both sides have a location and they are unrelated

# Minimum difflib ratio for LOCATION_FUZZY. High on purpose: "Pal" and
# "Palanpur" are not the same place.
LOCATION_FUZZY_RATIO = 0.85

# Words that carry no locality of their own — a shared "road" or "gam" must
# never read as a shared AREA. Anything here is dropped before two area
# names are compared word by word.
AREA_STOP_WORDS: FrozenSet[str] = frozenset(
    {
        "road", "rd", "street", "st", "lane", "marg", "highway", "circle", "cross",
        "area", "zone", "sector", "block", "near", "nr", "opp", "opposite", "behind",
        "main", "new", "old", "the", "and", "gam", "village", "town", "city",
        "east", "west", "north", "south", "side", "chowk", "char", "rasta",
    }
)

# Shortest word that can identify a locality on its own.
AREA_MIN_WORD_LENGTH = 3

# Localities this deployment knows to be adjacent — locality -> the ones
# next to it, both sides written the way people write them (lowercased when
# read). EMPTY BY DEFAULT and deliberately so: a guessed adjacency is a
# guessed match, and the engine must never invent geography it was not
# given. Fill it in for the areas the business actually works in and the
# LOCATION_NEARBY tier starts applying to them.
NEARBY_AREAS: Mapping[str, Tuple[str, ...]] = {}

# City names, for the "same city, different locality" tier. Used only to
# recognise a CITY when one is written — never to guess which city a
# locality belongs to.
#
# It also decides how a client's own area list is read: a client who wrote
# nothing but a city ("Ahmedabad") has stated that city as their area, so a
# listing in it is an EXACT match. A client who wrote a locality AND a city
# ("Vesu, Surat") has stated the locality — a listing elsewhere in Surat is
# same-city, not exact.
CITY_NAMES: FrozenSet[str] = frozenset(
    {
        "surat", "ahmedabad", "amdavad", "vadodara", "baroda", "rajkot", "bharuch",
        "anand", "navsari", "valsad", "bhavnagar", "gandhinagar", "jamnagar",
        "junagadh", "mehsana", "morbi", "palanpur", "porbandar", "vapi", "ankleshwar",
        "mumbai", "pune", "delhi", "bengaluru", "bangalore", "hyderabad", "chennai",
        "kolkata", "jaipur", "indore", "nagpur", "udaipur",
    }
)


# =========================================================================
# CONFIDENCE
#
# A SEPARATE number answering a different question: how much do we actually
# know? It is never mixed into the match score and never used to lower one.
# "92% match / Low confidence" is an honest, useful thing to show a broker;
# silently rewriting that 92 into a 76 is not.
# =========================================================================

# How much of a complete brief each stated requirement represents. The four
# that matter plus a free-text description sum to 1.0; size and furnishing
# are a bonus on top (confidence is capped at 1.0, so stating a size can
# never make a thin brief look complete).
CONFIDENCE_WEIGHTS: Dict[str, float] = {
    "budget": 0.32,
    "location": 0.24,
    "property_type": 0.20,
    "bhk": 0.16,
    "semantic": 0.08,
    "size": 0.08,
    "furnishing": 0.06,
}

# What a stated requirement still counts for when THIS property cannot
# answer it. Not zero — we do know what the client wants — but most of the
# weight is withheld, because a judgement made against missing data is not a
# confident one. This is the half of confidence that reflects the PROPERTY:
# a listing with no price against a client who gave a budget is a less
# certain match than one with a price, and says so.
UNANSWERED_CONFIDENCE_CREDIT = 0.25

# Purpose is not in CONFIDENCE_WEIGHTS: it is a hard filter with only two
# outcomes, so knowing it tells us almost nothing extra about WHAT the
# client wants. It is still scored and still explained.


# =========================================================================
# BUCKETS
#
# Buckets are read off the real scores. Nothing is ever rescaled, clipped or
# nudged to land in one.
# =========================================================================

MATCH_HIGH_CUTOFF = 0.85
MATCH_MEDIUM_CUTOFF = 0.65

# Set to the EXACT sums of the briefs they are meant to admit, rather than to
# round numbers near them — otherwise the boundary falls in an arbitrary place
# and two briefs a broker would call equally informative land in different
# buckets.
#
# High = any three of the four requirements that matter, as long as a budget
# is one of them. The weakest such brief is budget + property type + BHK
# (0.32 + 0.20 + 0.16 = 0.68), so that is the cutoff. At 0.72 — the obvious
# round choice — "budget, Flat, 3 BHK" would have been only Medium, although
# it says what the client wants and what they can spend and leaves only the
# area open.
CONFIDENCE_HIGH_CUTOFF = 0.68

# Medium = any two of the four. The weakest such brief is budget + BHK
# (0.32 + 0.16 = 0.48). At 0.50 that combination missed Medium by two
# hundredths — and it is the second most common brief in this database
# (57 of 455 clients), so the arbitrary two hundredths would have mattered a
# great deal.
CONFIDENCE_MEDIUM_CUTOFF = 0.48

# Below this a property is not stored or shown at all, in any bucket. It is
# a floor on the MATCH score only — a poor fit against what the client asked
# for. A thin brief never removes anything: that is what confidence is for
# (see the module docstring's second paragraph, and §19 of the spec this
# engine implements — incomplete client information affects CONFIDENCE,
# explicit incompatibility affects ELIGIBILITY, and neither is allowed to
# quietly hide a property that genuinely fits what was said).
MIN_STORED_MATCH_SCORE = 0.45

# A field scoring at or above this counts as a MATCHED requirement in the
# explanation the broker reads.
MATCHED_FIELD_CUTOFF = 0.80

# Below this share of the client's stated brief answerable from the listing,
# the match is flagged "partial data" in the UI.
PARTIAL_EVIDENCE_CUTOFF = 0.6


# =========================================================================
# STORAGE
# =========================================================================

# How many matches one client ever keeps — their best, by the ranking order
# in scoring.ranking_key (match score first, confidence as the tie-break).
#
# A shortlist is only useful while a person can read it. Nobody calls, sends
# or visits the 900th-best property on a list, and holding one costs on
# every axis that matters here: rows in Neon, bytes over the wire on every
# dialog open, and rendering work in the browser.
#
# A ceiling, not a target: a well-specified client rarely comes near it.
# Applied identically to a full recompute and to every incremental/nightly
# merge, so the ceiling holds however a client's matches were arrived at,
# and always by keeping the HIGHEST-ranked — never an arbitrary hundred.
MAX_MATCHES_PER_CLIENT = 100
