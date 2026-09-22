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

# A BEDROOM COUNT MEANS A HOME.
#
# A brief very often names a BHK and no property type at all ("2 BHK Fully
# Furnished, Vesu"), and a missing type is correctly read as "no preference".
# But nobody asks for a plot or a shop by bedroom count, so a brief that asks
# for a BHK and names no type never matches a land or commercial listing.
#
# This was always the rule on the broker-requirement side and was simply
# missing on the client side — the two now share one gate, so both behave the
# same. Against this project's own data it removes 61 stored rows across 69
# clients, every one of them a Plot or a Commercial listing offered to
# somebody who had asked for bedrooms, and leaves no client without matches.
#
# Listings of UNKNOWN type are unaffected: only a type that positively says
# land or commercial is excluded (see normalization.is_non_residential_type).
REJECT_NON_RESIDENTIAL_FOR_BHK = True

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

# How far over the top of the target band a property may still be eligible.
# 0.10 = a ₹1cr ceiling still considers ₹1.10cr, and rejects ₹1.11cr.
#
# Was 0.25, which is a quarter of a client's whole budget: against 1,600
# listings a ₹1cr brief was keeping everything up to ₹1.25cr, and budget is
# the heaviest field there is — passing it was most of what it took to reach
# the shortlist, so almost every brief filled its hundred slots. Somebody who
# says "up to ₹1cr" will stretch a little for the right property; they will
# not stretch by a quarter.
BUDGET_OVER_TOLERANCE = 0.10

# The same, BELOW the bottom of the target band — and the half that did not
# exist at all until now.
#
# A budget used to be a one-sided filter: too expensive was rejected, too
# cheap never was, on the reasoning that a cheaper property is not a worse
# property. In a small database that is true. In this one it meant a ₹1cr
# brief kept ₹39L listings — a different kind of property in a different part
# of town for a different buyer — and they are not a bargain, they are noise
# sitting in a hundred-row shortlist that a real match then cannot get into.
#
# Applied to the BAND, not to the raw number, so it means the same thing
# whichever way the brief was written:
#   "up to ₹1cr"        -> band ₹65L–₹1cr, eligible from ₹58.5L to ₹1.10cr
#   "at least ₹1cr"     -> band ₹1cr–₹1.40cr, eligible from ₹90L to ₹1.54cr
#   "₹80L to ₹1cr"      -> band as written, eligible from ₹72L to ₹1.10cr
# Every figure above is a RATIO of what the client said, so it scales with
# the brief instead of being a rupee amount tuned for one price bracket.
BUDGET_UNDER_TOLERANCE = 0.10


# =========================================================================
# STAGE 2 — MATCH SCORE
#
# How well the property matches the requirements the client GAVE. The
# denominator is the weight of what the CLIENT asked for — never the weight
# of what this property happens to have data for. A field the client never
# stated is not scored and carries no weight at all.
# =========================================================================

MATCH_WEIGHTS: Dict[str, float] = {
    # 0.35, down from 0.40. Budget is still the heaviest single field and
    # should be — but it was heavy enough that clearing its (very wide)
    # tolerance was most of what a property needed to reach the shortlist.
    # The real fix is the narrower band above; this is the smaller half of
    # it, and it is deliberately small: a budget miss must still hurt.
    "budget": 0.35,
    "location": 0.30,
    "bhk": 0.20,
    # 0.16, up from 0.10. Both sides of this comparison carry real prose now
    # — a brief's "additional requirements" and a listing's description are
    # embedded into the vectors compared here — and the people writing the
    # briefs are putting more and more into that box, so the field has to
    # count for more than a rounding error. Raised, not promoted: it is still
    # smaller than budget, location and BHK, it still sits behind the
    # eligibility gate, and it still cannot rescue an incompatible property.
    #
    # The ceiling on this number is that a property meeting every structured
    # requirement must stay a High match even when its description has
    # nothing in common with the brief. With the weights around it that holds
    # up to ~0.16 and stops holding above it, which is why it is 0.16 and not
    # 0.20 (see tests/test_matching_engine.py's "it stays a low weight
    # signal").
    "semantic": 0.16,
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
# someone shopping there is looking in roughly ₹65L–₹1cr, and a ₹39L listing
# is a different kind of property, not a bargain. A client who gave their own
# minimum never reaches this.
#
# No longer a purely soft floor: BUDGET_UNDER_TOLERANCE now puts a hard edge
# a little way below it (₹58.5L against a ₹1cr ceiling), so the band is both
# where a property scores full marks AND, plus that tolerance, how far down
# the engine will look at all.
BUDGET_TARGET_BAND = 0.65

# The mirror of BUDGET_TARGET_BAND for a brief that gives a floor and no
# ceiling ("at least ₹1cr", "3 BHK above ₹80L"). Without it such a brief had
# NO top at all: every listing above the floor scored a perfect 1.0 on
# budget, so a ₹1cr minimum was treating a ₹6cr bungalow as an ideal price
# match and ranking it above a ₹1.1cr flat. "At least ₹1cr" describes a
# bracket, not an open-ended appetite — the ones worth showing are ₹1cr to
# roughly ₹1.4cr.
BUDGET_OPEN_TOP_BAND = 1.40

# The budget curve, as (how far outside the band, as a fraction of the
# nearest bound -> score) anchor points, linearly interpolated between.
# Inside the band is 1.0.
#
# Both curves are now steep and SHORT, because both sides of the band end in
# a hard edge a tenth of the way out (BUDGET_OVER_TOLERANCE /
# BUDGET_UNDER_TOLERANCE) — nothing further out than the last anchor can
# reach scoring at all. The old curves ran out to 60% over and 100% under,
# which is what let a badly-priced property still score 0.65 on the field
# that matters most.
BUDGET_OVER_ANCHORS: Tuple[Tuple[float, float], ...] = (
    (0.0, 1.0),
    (0.02, 0.90),
    (0.05, 0.72),
    (0.10, 0.45),
)
BUDGET_UNDER_ANCHORS: Tuple[Tuple[float, float], ...] = (
    (0.0, 1.0),
    (0.02, 0.90),
    (0.05, 0.72),
    (0.10, 0.45),
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

# Shortest word that may be matched FUZZILY at all. A near-spelling test is
# only meaningful on a word long enough for one changed letter to be a small
# fraction of it: "Piplod"/"Pipplod" is obviously the same place, but at
# three and four letters a single letter is a quarter of the word and the
# ratio above stops separating a typo from a different locality — "Pal" and
# "Pali" score 0.86 and are two real, different areas.
#
# Deliberately higher than AREA_MIN_WORD_LENGTH: a short area name still
# matches EXACTLY and still counts as a distinctive word (LOCATION_PARTIAL).
# All that is withheld from it is the guess.
LOCATION_FUZZY_MIN_WORD_LENGTH = 4

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

# The same ceiling for the DEMAND side — one broker requirement's stored
# matches (Service/BrokerRequirementService/requirement_matching_service.
# best_matches). Named separately so the two can be tuned apart if a
# requirement ever wants a different shortlist length, and equal by default
# because a requirement and a client inquiry are the same thing said by two
# different people.
MAX_MATCHES_PER_REQUIREMENT = MAX_MATCHES_PER_CLIENT
