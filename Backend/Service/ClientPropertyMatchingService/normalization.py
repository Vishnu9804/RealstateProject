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
from typing import List, Optional, Tuple

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


_NON_RESIDENTIAL_WORDS_RE = re.compile(
    r"\b(?:plots?|land|shops?|offices?|showrooms?|warehouses?|godowns?|commercial|industrial|factory|shed)\b"
)


def is_non_residential_type(raw: Optional[str]) -> bool:
    """True when a property type is land or commercial — a kind of property
    nobody asks for by bedroom count. Unknown/empty is NOT non-residential
    (False): only a type that positively says land/commercial counts.

    Used only by the broker-requirement side (Service/BrokerRequirementService/
    requirement_matching_service.py), never by property_type_gate or anything
    else the client-inquiry scoring runs, so client matching is unaffected."""
    if not raw:
        return False
    token = canonical_type_token(raw)
    family = _family_of(token)
    if family is not None:
        return family is _LAND or family is _COMMERCIAL
    return _NON_RESIDENTIAL_WORDS_RE.search(token) is not None


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

# Comparison wording — "more than 3", "less than 3", "up to 3", "3 or less".
# Before these existed, "more than 3 BHK" matched none of _MIN_WORDS_RE's
# words and fell through to a plain "exact 3": the 3 BHKs landed in High and
# the 4/5 BHKs the client actually asked for were pushed down to Low.
#
# Each pattern is tied to the number it qualifies (qualifier right before it,
# or "or less"/"or more" right after it), so a stray word elsewhere in the
# text — "3 BHK under construction" — never turns into a bound. They are
# tried in this order, and every match is blanked out before the next
# pattern runs, so "not more than 3" is read once as "at most 3" and never a
# second time as "more than 3", and ">= 3" is never re-read as "> 3".
#
# The last flag marks wording _MIN_WORDS_RE already understood ("at least",
# "minimum", "above", "or more", "+"). Those are collected too, so "at least
# 2 but less than 5" keeps both ends, but on their own they never switch a
# requirement over to the new range reading — every value that parsed before
# this existed still parses, and scores, exactly as it did.
_NUM = r"(\d+(?:\.\d+)?)"
_UNIT = r"(?:\s*(?:bhk|b\.h\.k\.?|rk|bed(?:room)?s?))?"
_MORE = r"(?:more|greater|bigger|larger|higher)"
_LESS = r"(?:less|fewer|smaller|lower)"
_BHK_BOUND_PATTERNS = (
    (re.compile(r"\bnot?\s+" + _MORE + r"\s+than\s*" + _NUM), "upper", True, False),
    (re.compile(r"\bnot?\s+" + _LESS + r"\s+than\s*" + _NUM), "lower", True, False),
    (re.compile(r"\b" + _MORE + r"\s+than\s+or\s+equal\s+to\s*" + _NUM), "lower", True, False),
    (re.compile(r"\b" + _LESS + r"\s+than\s+or\s+equal\s+to\s*" + _NUM), "upper", True, False),
    (re.compile(r"(?:>=|≥)\s*" + _NUM), "lower", True, False),
    (re.compile(r"(?:<=|≤)\s*" + _NUM), "upper", True, False),
    (re.compile(r"\b" + _MORE + r"\s+than\s*" + _NUM), "lower", False, False),
    (re.compile(r"\b" + _LESS + r"\s+than\s*" + _NUM), "upper", False, False),
    (re.compile(r">\s*" + _NUM), "lower", False, False),
    (re.compile(r"<\s*" + _NUM), "upper", False, False),
    (re.compile(r"\b(?:below|under)\s*" + _NUM), "upper", False, False),
    (re.compile(r"\b(?:up\s*to|at\s*most|max(?:imum)?(?:\s+of)?)\s*" + _NUM), "upper", True, False),
    (re.compile(_NUM + _UNIT + r"\s*(?:or|and|&)\s*(?:less|fewer|below|under)\b"), "upper", True, False),
    (re.compile(r"\b(?:at\s*least|min(?:imum)?(?:\s+of)?|above)\s*" + _NUM), "lower", True, True),
    (
        re.compile(_NUM + _UNIT + r"\s*(?:\+|(?:or|and|&)\s*(?:more|above)\b|min(?:imum)?\b|at\s*least\b)"),
        "lower",
        True,
        True,
    ),
)


class BhkIntent:
    """Parsed shape of a free-text BHK requirement.

    - "minimum": "minimum 3", "3+", "3 or more" -> N and anything above is fine.
    - "range": any comparison wording — "more than 3" (4, 5, ... fine),
      "less than 3", "up to 3", "3 or less", "more than 2 and less than 5".
      `lower`/`upper` are the bounds (None = open on that side), and each
      `*_inclusive` says whether the bound value itself is fine: "more than 3"
      is lower=3 exclusive, "at least 3" would be lower=3 inclusive.
    - "set": "3 or 4", "2/3 BHK" -> any listed value is fine.
    - "exact": a bare number/phrase with no qualifier, e.g. "3 BHK" -> 3 is
      the target, with graceful decay for neighbours (see bhk_score).
    - "exact_strict": "exactly 3 BHK", "only 3 BHK" -> same target, but a
      steeper decay — a 4 BHK should score meaningfully worse here than
      against a plain "3 BHK", per the feature spec's distinction.
    """

    __slots__ = ("kind", "values", "lower", "lower_inclusive", "upper", "upper_inclusive")

    def __init__(
        self,
        kind: str,
        values: List[float],
        lower: Optional[float] = None,
        lower_inclusive: bool = True,
        upper: Optional[float] = None,
        upper_inclusive: bool = True,
    ):
        self.kind = kind
        self.values = values
        self.lower = lower
        self.lower_inclusive = lower_inclusive
        self.upper = upper
        self.upper_inclusive = upper_inclusive


def _parse_bhk_range(text: str) -> Optional[BhkIntent]:
    """A "range" intent when the text uses comparison wording, else None (the
    caller then parses it exactly as it always has — see _BHK_BOUND_PATTERNS)."""
    lowers: List[Tuple[float, bool]] = []
    uppers: List[Tuple[float, bool]] = []
    has_comparison = False
    remaining = text
    for pattern, side, inclusive, already_understood in _BHK_BOUND_PATTERNS:
        for match in pattern.finditer(remaining):
            (lowers if side == "lower" else uppers).append((float(match.group(1)), inclusive))
            has_comparison = has_comparison or not already_understood
        remaining = pattern.sub(lambda match: " " * len(match.group(0)), remaining)

    if not has_comparison:
        return None

    # Several bounds on one side ("at least 2, more than 3") — the tightest
    # wins; at the same value an exclusive bound is the tighter one.
    lower = max(lowers, key=lambda bound: (bound[0], not bound[1])) if lowers else None
    upper = min(uppers, key=lambda bound: (bound[0], bound[1])) if uppers else None

    if lower is not None and upper is not None:
        impossible = lower[0] > upper[0] or (lower[0] == upper[0] and not (lower[1] and upper[1]))
        if impossible:
            return None  # "more than 5, less than 3" — not a range; don't guess one

    return BhkIntent(
        "range",
        [],
        lower=lower[0] if lower else None,
        lower_inclusive=lower[1] if lower else True,
        upper=upper[0] if upper else None,
        upper_inclusive=upper[1] if upper else True,
    )


def parse_bhk_intent(raw: Optional[str]) -> Optional[BhkIntent]:
    if not raw:
        return None
    text = raw.lower()
    numbers = [float(n) for n in _BHK_NUM_RE.findall(text)]
    if not numbers:
        return None

    range_intent = _parse_bhk_range(text)
    if range_intent is not None:
        return range_intent
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

    if intent.kind == "range":
        distance = _range_distance(p, intent)
        return 1.0 if distance == 0 else _decay(distance, steep=False)

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


def _range_distance(p: float, intent: BhkIntent) -> float:
    """How far a property's BHK is from the nearest value the range accepts,
    0 when it is inside. An exclusive bound adds one step: against "more
    than 3", a 3 BHK is one away from the nearest acceptable 4 — the same
    distance a 2 BHK is from "at least 3" — so the bound value itself is a
    real but mild miss, never a match."""
    if intent.lower is not None and (p < intent.lower or (p == intent.lower and not intent.lower_inclusive)):
        return intent.lower - p + (0 if intent.lower_inclusive else 1)
    if intent.upper is not None and (p > intent.upper or (p == intent.upper and not intent.upper_inclusive)):
        return p - intent.upper + (0 if intent.upper_inclusive else 1)
    return 0.0


def _decay(distance: float, steep: bool) -> float:
    """Bare/minimum/set intents: 1 away -> 0.5, 2 away -> 0.25, decaying
    gently — a neighbouring BHK is a real but mild mismatch. "exactly N"
    intents decay much faster (1 away -> ~0.25, 2 away -> ~0.06), since the
    client explicitly ruled out anything else."""
    base = 0.25 if steep else 0.5
    floor = 0.02 if steep else 0.05
    return max(floor, base**distance)
