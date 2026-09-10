"""Normalization helpers for the free-text fields the scoring engine
(scoring.py) can't compare with a plain string equality: property-type
synonym/family grouping and BHK intent parsing. Both are plain regex/lookup
logic, deliberately — this project's "free, local-only" constraint on the
embedding model (Service/WhatsAppDataFetchingService/embedding_service.py)
applies just as much to a new LLM call here; a person's property-type or
BHK wording is simple enough not to need one.
"""

from __future__ import annotations

import difflib
import re
from typing import List, Optional

# --- property type -----------------------------------------------------

# Broad families a property type can belong to. Within a family, types are
# near-synonyms (flat/apartment) or close siblings (villa/bungalow/row
# house); across families is a real mismatch (see property_type_gate).
_RESIDENTIAL_FLAT = frozenset(
    {
        "flat",
        "flats",
        "apartment",
        "apartments",
        "flat/apartment",
        # A penthouse is the top-floor unit of an apartment building — the
        # same kind of property as a flat, in the same kind of building,
        # bought the same way. Someone who asks for a Flat should be shown
        # one; someone who asks for a Penthouse should not be shown a plot.
        "penthouse",
        "penthouses",
        "pent house",
    }
)
_RESIDENTIAL_HOUSE = frozenset(
    {"villa", "villas", "bungalow", "bungalows", "row house", "rowhouse", "independent house", "house", "duplex"}
)
# "land/plot" is here for the same reason "flat/apartment" is in the set
# above: a CLIENT's value is split on "/" before it gets here, but a
# PROPERTY's is not — and "Land/Plot" is one of the literal type names the
# extractor is told to use (Agent/WhatsAppDataFetchingAgent/
# glm_extraction_schema.py), so without it every plot listing falls through
# to the fuzzy-text branch and scores 0.15 against a plot-hunting client.
_LAND = frozenset({"plot", "plots", "land", "farmland", "agricultural land", "farm land", "land/plot", "plot/land"})
_COMMERCIAL = frozenset(
    {"shop", "shops", "office", "offices", "showroom", "warehouse", "commercial space", "commercial", "godown"}
)
_FAMILIES = (_RESIDENTIAL_FLAT, _RESIDENTIAL_HOUSE, _LAND, _COMMERCIAL)

_TYPE_SPLIT_RE = re.compile(r"\bor\b|\bbut open to\b|\balso consider\b|\bwilling to consider\b|,|/")
# Filler words that can sit next to a type name without a separator
# ("flat preferred", "villa also ok") — stripped so the remaining token
# still matches its synonym/family group instead of falling through to the
# fuzzy-text fallback for no good reason.
_FILLER_WORDS_RE = re.compile(r"\bpreferred\b|\bideally\b|\bmust be\b")

# "Duplex" describes a unit's INTERNAL layout — living space spread over two
# floors — not what kind of property it is. A "duplex flat" is a flat; a
# "penthouse duplex" is a penthouse. Treating the modifier as a type of its
# own is how a client who asked for a Flat ends up never seeing a duplex
# flat: the words don't match, neither belongs to the other's family, and
# the pair falls through to the fuzzy-text branch below and scores 0.15.
#
# So it is stripped whenever a real type is left behind, on BOTH sides —
# "flat duplex" and "duplex flat" and "flat" all reduce to "flat". A BARE
# "duplex" keeps its own meaning (a two-storey house, see
# _RESIDENTIAL_HOUSE), which is why the fallback below matters.
_TYPE_MODIFIERS_RE = re.compile(r"\b(?:duplex|simplex|triplex)\b")


def normalize_token(value: str) -> str:
    return " ".join(value.lower().strip().split())


def canonical_type_token(value: str) -> str:
    """The comparable form of one property-type token: lowercased, collapsed,
    and with layout modifiers dropped — unless dropping them would leave
    nothing, in which case the modifier WAS the type and is kept as-is."""
    token = normalize_token(value)
    if not token:
        return token
    return normalize_token(_TYPE_MODIFIERS_RE.sub(" ", token)) or token


def _family_of(token: str) -> Optional[frozenset]:
    for family in _FAMILIES:
        if token in family:
            return family
    return None


def split_client_property_types(raw: Optional[str]) -> List[str]:
    """A client's property_type is free text and may list more than one
    acceptable type: "flat or apartment", "flat/villa",
    "flat preferred, but open to villa". Splits into normalized tokens,
    preserving order — the FIRST token is the primary preference (see
    property_type_gate's `is_primary`), later ones are secondary/"also
    open to" preferences."""
    if not raw:
        return []
    tokens = []
    for part in _TYPE_SPLIT_RE.split(raw.lower()):
        token = canonical_type_token(_FILLER_WORDS_RE.sub("", part))
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def property_type_gate(client_raw: Optional[str], property_raw: Optional[str]) -> float:
    """Critical-gate factor in [0, 1] — see scoring.py. Missing on the
    client side means no stated preference, so every type is open (1.0):
    a client who only gave a budget and area should still see a shop or a
    plot show up, per the feature spec. Missing on the property side is
    genuinely unknown, not a match or a miss, so it gets a neutral 0.5
    rather than either extreme."""
    client_tokens = split_client_property_types(client_raw)
    if not client_tokens:
        return 1.0
    if not property_raw:
        return 0.5
    prop_token = canonical_type_token(property_raw)
    return max(_pair_compatibility(token, prop_token, is_primary=(i == 0)) for i, token in enumerate(client_tokens))


def _pair_compatibility(client_token: str, prop_token: str, is_primary: bool) -> float:
    if client_token == prop_token:
        # A secondary ("also open to villa") mention that happens to match
        # exactly still isn't the client's stated preference — score it
        # high, but not the full 1.0 reserved for the primary match.
        return 1.0 if is_primary else 0.85

    client_family = _family_of(client_token)
    prop_family = _family_of(prop_token)

    if client_family is None or prop_family is None:
        # Wording outside the known synonym groups — fall back to fuzzy
        # text similarity rather than guessing a family for it.
        ratio = difflib.SequenceMatcher(None, client_token, prop_token).ratio()
        return 0.6 if ratio > 0.6 else 0.15

    if client_family is prop_family:
        return 1.0 if is_primary else 0.85  # same synonym group, e.g. flat <-> apartment

    residential_pair = {client_family, prop_family} == {_RESIDENTIAL_FLAT, _RESIDENTIAL_HOUSE}
    if residential_pair:
        # A secondary ("but open to villa") mention scores higher than an
        # unmentioned villa would — the client explicitly said they'd
        # consider it — but still below an exact/primary match.
        return 0.8 if not is_primary else 0.55

    # Cross broad category (residential vs. land, residential vs.
    # commercial, land vs. commercial) — a severe mismatch. Capped low, not
    # zero, so it's still visible/explainable at the bottom of Low rather
    # than silently excluded (see the feature discussion's flat-vs-plot
    # example: this should never score "reasonably high", but a real
    # record beats a hard exclusion when nothing better exists).
    return 0.08


# --- BHK -----------------------------------------------------------------

_BHK_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)")
_MIN_WORDS_RE = re.compile(r"\bmin(?:imum)?\b|\+|\babove\b|\bat ?least\b|\bor more\b")
_STRICT_WORDS_RE = re.compile(r"\bexactly\b|\bonly\b|\bstrictly\b")


class BhkIntent:
    """Parsed shape of a free-text BHK requirement.

    - "minimum": "minimum 3", "3+", "3 or more" -> N and anything above is fine.
    - "set": "3 or 4", "2/3 BHK" -> any listed value is fine.
    - "exact": a bare number/phrase with no qualifier, e.g. "3 BHK" -> 3 is
      the target, with graceful decay for neighbours (see bhk_score).
    - "exact_strict": "exactly 3 BHK", "only 3 BHK" -> same target, but a
      steeper decay — a 4 BHK should score meaningfully worse here than
      against a plain "3 BHK", per the feature spec's distinction.
    """

    __slots__ = ("kind", "values")

    def __init__(self, kind: str, values: List[float]):
        self.kind = kind
        self.values = values


def parse_bhk_intent(raw: Optional[str]) -> Optional[BhkIntent]:
    if not raw:
        return None
    text = raw.lower()
    numbers = [float(n) for n in _BHK_NUM_RE.findall(text)]
    if not numbers:
        return None

    if _MIN_WORDS_RE.search(text):
        return BhkIntent("minimum", [min(numbers)])
    if len(numbers) >= 2:
        return BhkIntent("set", numbers)
    if _STRICT_WORDS_RE.search(text):
        return BhkIntent("exact_strict", numbers)
    return BhkIntent("exact", numbers)


def bhk_score(client_raw: Optional[str], property_raw: Optional[str]) -> Optional[float]:
    """Soft field score in [0, 1], or None if either side has no usable
    BHK data (not comparable — never scored as a match or a mismatch)."""
    intent = parse_bhk_intent(client_raw)
    if intent is None or not property_raw:
        return None
    prop_numbers = [float(n) for n in _BHK_NUM_RE.findall(property_raw.lower())]
    if not prop_numbers:
        return None
    p = prop_numbers[0]

    if intent.kind == "minimum":
        target = intent.values[0]
        return 1.0 if p >= target else _decay(target - p, steep=False)

    if intent.kind == "set":
        low, high = min(intent.values), max(intent.values)
        if low <= p <= high:
            return 1.0
        distance = (low - p) if p < low else (p - high)
        return _decay(distance, steep=False)

    steep = intent.kind == "exact_strict"
    target = intent.values[0]
    distance = abs(p - target)
    return 1.0 if distance == 0 else _decay(distance, steep=steep)


def _decay(distance: float, steep: bool) -> float:
    """Bare/minimum/set intents: 1 away -> 0.5, 2 away -> 0.25, decaying
    gently — a neighbouring BHK is a real but mild mismatch. "exactly N"
    intents decay much faster (1 away -> ~0.25, 2 away -> ~0.06), since the
    client explicitly ruled out anything else."""
    base = 0.25 if steep else 0.5
    floor = 0.02 if steep else 0.05
    return max(floor, base**distance)
