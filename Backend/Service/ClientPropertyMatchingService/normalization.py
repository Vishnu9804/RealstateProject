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
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from Service.ClientPropertyMatchingService import match_config as config

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
    {
        "villa",
        "villas",
        "bungalow",
        "bungalows",
        "row house",
        "rowhouse",
        "independent house",
        "house",
        "duplex",
        # Offered by the requirements form. A house on its own land — a
        # sibling of the bungalow, and without an entry here "farmhouse"
        # and "farm house" wouldn't even match each other.
        "farm house",
        "farmhouse",
        "farm houses",
        "farmhouses",
    }
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


def split_type_groups(raw: Optional[str]) -> List[str]:
    """The property types a CLIENT picked, one entry per type, as written.

    The requirements form lets a client pick several types and stores them
    comma-separated ("Flat, Bungalow"). Each is an equal preference, scored
    on its own (see scoring.score_client_property) — unlike
    split_client_property_types' "first is primary" reading, which is what
    free text like "flat preferred, but open to villa" means. A value with
    no comma — every client stored before multi-select, "Land/Plot",
    "flat or villa" — is exactly one group and scores exactly as before.

    Client side only: a broker requirement's comma list means "main type
    first" and keeps going through property_type_gate whole."""
    groups: List[str] = []
    seen = set()
    for part in (raw or "").split(","):
        label = " ".join(part.split())
        if label and label.lower() not in seen:
            seen.add(label.lower())
            groups.append(label)
    return groups


def size_for(sizes: Optional[dict], group: Optional[str]) -> Optional[str]:
    """The size a client gave for one of their type groups, if any.
    Case-insensitive on the key, so a stored "Flat" still answers "flat"."""
    if not sizes or not group:
        return None
    key = group.strip().lower()
    for name, text in sizes.items():
        if str(name).strip().lower() == key and text and str(text).strip():
            return str(text).strip()
    return None


# --- size ------------------------------------------------------------------
#
# A client's size preference is free text in any format and any language
# ("1200 sqft", "1000-1500", "around 200 vaar", "૨૦૦ વાર", "min 2 vigha"),
# so it is read here into a plain square-feet range, the same unit a
# property's carpet area is converted into. Anything that can't be read
# confidently comes back as None — never scored, never a mismatch.

# Types whose size people give in vaar (square yards) rather than sq ft —
# the same rule the requirements form uses to label each size box. Only
# decides the unit of a bare number: a unit written in the text always wins.
_VAAR_TYPE_RE = re.compile(r"bungalow|land|plot|farm")

# Square feet per unit. Vaar/gaj is a square yard (exactly 9 sq ft);
# vigha is Gujarat's 16 guntha, and a guntha is 121 sq yards (1,089 sq ft).
_SQFT_PER_UNIT = {
    "sqft": 1.0,
    "vaar": 9.0,
    "sqm": 10.7639,
    "guntha": 1089.0,
    "vigha": 17424.0,
    "acre": 43560.0,
}


def _latin_word(pattern: str) -> str:
    # Letter lookarounds rather than \b, so "1200sqft" and "200vaar" (no
    # space after the number) still find their unit.
    return rf"(?<![a-z])(?:{pattern})(?![a-z])"


# Gujarati and Hindi spellings are matched as plain substrings: \b is not
# reliable around their combining vowel signs.
_SIZE_UNIT_PATTERNS = tuple(
    (unit, re.compile(pattern))
    for unit, pattern in (
        ("sqft", _latin_word(r"sq\.?\s*f(?:ee)?t|square\s*f(?:ee|oo)t|ft|feet|foot") + "|ફૂટ|ફુટ|फीट|फुट"),
        ("vaar", _latin_word(r"vaar|var|waar|gaj|sq\.?\s*y(?:ar)?ds?|square\s*yards?|yards?") + "|વાર|ગજ|गज"),
        ("sqm", _latin_word(r"sq\.?\s*m(?:trs?|eters?|etres?)?|square\s*met(?:er|re)s?|met(?:er|re)s?|mtrs?") + "|મીટર|मीटर"),
        ("guntha", _latin_word(r"gunthas?|guntas?") + "|ગુંઠા|गुंठा"),
        ("vigha", _latin_word(r"vighas?|bighas?") + "|વીઘા|વિઘા|बीघा"),
        ("acre", _latin_word(r"acres?") + "|એકર|एकड़"),
    )
)
_LOCAL_DIGITS = str.maketrans("૦૧૨૩૪૫૬૭૮૯०१२३४५६७८९", "01234567890123456789")
# "1,200" and "1,00,000" are one number; "1200,1500" is two.
_DIGIT_GROUP_COMMA_RE = re.compile(r"(?<=\d),(?=\d{2,3}(?!\d))")
_SIZE_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
# A number that is a bedroom count ("3 BHK, 1500 sqft") is not a size.
_BEDROOM_AFTER_RE = re.compile(r"\s*(?:bhk|rk|bed)")
_SIZE_AT_LEAST_RE = re.compile(
    r"\+|\bmin(?:imum)?\b|\bat\s*least\b|\babove\b|\bmore\s+than\b|\bover\b|\bor\s+more\b|\bplus\b"
    r"|થી\s*વધુ|થી\s*વધારે|से\s*ज्यादा|से\s*अधिक"
)
_SIZE_AT_MOST_RE = re.compile(
    r"\bmax(?:imum)?\b|\bup\s*to\b|\bbelow\b|\bunder\b|\bless\s+than\b|\bwithin\b|\bat\s*most\b|\bor\s+less\b"
    r"|સુધી|तक"
)
# A single number with no qualifier ("1200 sqft") is taken as "about that":
# people rarely know the exact figure, and a 1,150 sq ft flat is what
# someone asking for 1,200 means.
_SIZE_APPROX_BAND = 0.10


def default_size_unit(group: Optional[str]) -> str:
    return "vaar" if group and _VAAR_TYPE_RE.search(group.lower()) else "sqft"


def _first_unit(segment: str) -> Optional[str]:
    found = None
    for unit, pattern in _SIZE_UNIT_PATTERNS:
        match = pattern.search(segment)
        if match and (found is None or match.start() < found[0]):
            found = (match.start(), unit)
    return found[1] if found else None


def parse_size_requirement(
    text: Optional[str], group: Optional[str]
) -> Optional[Tuple[Optional[float], Optional[float]]]:
    """(lowest, highest) acceptable size in square feet — either end None
    when open — or None when nothing usable was written.

    Each number takes the unit written after it; a number with none takes
    the next one's ("150 to 200 vaar"), else the type's usual unit (see
    default_size_unit). Two or more numbers are a range; one number is a
    minimum or a maximum when worded that way, and "about that" otherwise."""
    if not text:
        return None
    cleaned = _DIGIT_GROUP_COMMA_RE.sub("", text.translate(_LOCAL_DIGITS).lower())
    matches = list(_SIZE_NUMBER_RE.finditer(cleaned))
    numbers: List[Tuple[float, Optional[str]]] = []
    for index, match in enumerate(matches):
        tail = cleaned[match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)]
        if _BEDROOM_AFTER_RE.match(tail):
            continue
        numbers.append((float(match.group()), _first_unit(tail)))
    fallback = default_size_unit(group)
    values = []
    for index, (number, unit) in enumerate(numbers):
        unit = unit or next((later for _, later in numbers[index + 1 :] if later), None) or fallback
        if number > 0:
            values.append(number * _SQFT_PER_UNIT[unit])
    if not values:
        return None
    if len(values) >= 2:
        return min(values), max(values)
    value = values[0]
    if _SIZE_AT_LEAST_RE.search(cleaned):
        return value, None
    if _SIZE_AT_MOST_RE.search(cleaned):
        return None, value
    return value * (1 - _SIZE_APPROX_BAND), value * (1 + _SIZE_APPROX_BAND)


def property_area_sqft(area_sqft: Optional[float], area_vaar: Optional[float]) -> Optional[float]:
    """A property's area in square feet, for comparison against a client's
    own size preference (which parse_size_requirement has already reduced to
    square feet too).

    A property stores its area in whichever unit the listing used, in its own
    column (see StructuredProperty.area_sqft/area_vaar) — no unit label to
    misread, so this is a plain lookup rather than a guess. Square feet wins
    when a listing quoted both, simply because it needs no conversion at all.
    None when neither column holds a usable number, which scoring treats as
    "not comparable" rather than as a miss."""
    if area_sqft is not None and area_sqft > 0:
        return area_sqft
    if area_vaar is not None and area_vaar > 0:
        return area_vaar * _SQFT_PER_UNIT["vaar"]
    return None


# --- furnishing ------------------------------------------------------------
#
# The three values BOTH sides are written with — a property's furnishing is
# normalized onto them by the extractor (Agent/WhatsAppDataFetchingAgent/
# glm_extraction_schema.py) and a client/broker requirement picks one of them
# from a dropdown. Kept here, next to the scoring helper that reads them, so
# the vocabulary has exactly one home: the property form
# (Frontend/src/components/PropertyFormDialog.tsx's FURNISHING_OPTIONS), the
# requirement form and the public requirements form all spell them the same.
UNFURNISHED = "Unfurnished"
SEMI_FURNISHED = "Semi furnished"
FULLY_FURNISHED = "Fully furnished"
FURNISHING_OPTIONS = (FULLY_FURNISHED, SEMI_FURNISHED, UNFURNISHED)

# 0 = nothing, 1 = some, 2 = everything. A plain ladder, because that is what
# furnishing actually is: "semi furnished" sits between the other two, and a
# miss by one step is a far smaller miss than a miss by two.
_FURNISHING_LEVEL = {UNFURNISHED: 0, SEMI_FURNISHED: 1, FULLY_FURNISHED: 2}

# Tried IN THIS ORDER, and every match is blanked out before the next pattern
# runs — which is the whole trick. "semi-furnished" contains "furnished" and
# "unfurnished" contains "furnish", so a naive "does it say furnished?" test
# reads all three as Fully furnished. Consuming the more specific wording
# first leaves nothing for the looser pattern to find.
_FURNISHING_PATTERNS = (
    (
        UNFURNISHED,
        re.compile(
            r"un[\s\-]*furnish\w*|non[\s\-]*furnish\w*|not[\s\-]*furnish\w*|with\s*out[\s\-]*furnitur\w*"
            r"|no[\s\-]*furnitur\w*|bare[\s\-]*shell|empty[\s\-]*(?:flat|house|unit)|naked",
            re.IGNORECASE,
        ),
    ),
    (
        SEMI_FURNISHED,
        re.compile(r"semi[\s\-]*furnish\w*|part(?:ly|ial(?:ly)?)[\s\-]*furnish\w*|half[\s\-]*furnish\w*", re.IGNORECASE),
    ),
    (
        FULLY_FURNISHED,
        # A bare mention only counts as "furnished" itself — the label word
        # "furnishing" on its own says nothing about the level, and must not
        # be read as one.
        re.compile(
            r"(?:full?y?|complete(?:ly)?)[\s\-]*furnish\w*|(?<![a-z])furnished|furnitur\w*\s*includ\w*",
            re.IGNORECASE,
        ),
    ),
)


def read_furnishing(text: Optional[str]) -> Optional[str]:
    """The furnishing level literally stated in `text`, as one of
    FURNISHING_OPTIONS — or None when the text states none, or states more
    than one ("2 BHK unfurnished, 3 BHK fully furnished"), where guessing
    either would be worse than leaving it unknown.

    Used for BOTH jobs a furnishing value needs: tidying a stored/typed value
    onto the vocabulary (canonical_furnishing) and recovering one the LLM left
    empty from the words a requirement actually used."""
    if not text or not text.strip():
        return None
    remaining = text
    found: List[str] = []
    for label, pattern in _FURNISHING_PATTERNS:
        if pattern.search(remaining):
            found.append(label)
            remaining = pattern.sub(" ", remaining)
    return found[0] if len(found) == 1 else None


def canonical_furnishing(raw: Optional[str]) -> Optional[str]:
    """A furnishing value rewritten onto FURNISHING_OPTIONS. A value that
    names no level at all is kept as written (whitespace collapsed) rather
    than dropped — an unusual but deliberate note a human typed stays theirs,
    exactly as canonical_bhk treats one."""
    if raw is None:
        return None
    text = " ".join(str(raw).split())
    if not text:
        return None
    return read_furnishing(text) or text


def furnishing_level(raw: Optional[str]) -> Optional[int]:
    """0/1/2 on the ladder above, or None when this side says nothing
    readable — which scoring treats as "not comparable", never as a miss."""
    canonical = canonical_furnishing(raw)
    return _FURNISHING_LEVEL.get(canonical) if canonical else None


def furnishing_score(client_raw: Optional[str], property_raw: Optional[str]) -> Optional[float]:
    """Soft field score in [0, 1], or None when either side has no readable
    furnishing (never scored as a match or a mismatch).

    Deliberately gentle, and deliberately low-weight in scoring.py: furnishing
    is the easiest thing about a property to change, so wanting a furnished
    flat and being shown a semi-furnished one is a nudge down the list, not a
    reason to bury it."""
    wanted = furnishing_level(client_raw)
    offered = furnishing_level(property_raw)
    if wanted is None or offered is None:
        return None
    return furnishing_distance_score(wanted, offered)


def furnishing_distance_score(wanted: int, offered: int) -> float:
    """The curve itself, over two already-read ladder positions.

    Split out of furnishing_score so the scoring engine can read a client's
    own level ONCE per client (scoring.ClientBrief) instead of re-running
    these regexes against the same requirement text for every property in
    the database. Identical arithmetic, same answers."""
    distance = abs(wanted - offered)
    if distance == 0:
        return 1.0
    return 0.55 if distance == 1 else 0.2


# --- location --------------------------------------------------------------
#
# Location is not a yes/no check, and it is not a guess either. The tiers in
# match_config (LOCATION_EXACT down to LOCATION_OTHER) are decided from the
# words both sides actually wrote — the client's own preferred areas against
# the listing's area/address/society — plus whatever adjacency the business
# has configured for itself (match_config.NEARBY_AREAS, empty by default).
# No geography is ever inferred: two localities are "nearby" only because
# someone said so, and a listing is "in the same city" only because a city
# name appears on both sides.

_AREA_WORD_RE = re.compile(r"[a-z0-9]+")

# One entry per distinct listing location text. Comfortably above the number
# of live listings, so a full rescore parses each one once; bounded so it can
# never become a leak.
_LOCATION_CACHE_SIZE = 8192


def client_area_tokens(preferred_areas: Optional[str]) -> List[str]:
    """A client's preferred_areas as comparable tokens, one per area they
    named. Comma- and slash-separated, because that is how the requirements
    form, the WhatsApp extraction and a broker requirement's joined list all
    write several areas."""
    if not preferred_areas:
        return []
    return [token for token in (normalize_token(part) for part in preferred_areas.replace("/", ",").split(",")) if token]


def _significant_words(text: str) -> List[str]:
    """The words in an area name that could identify a place on their own —
    "road", "gam", "near" and friends dropped (match_config.AREA_STOP_WORDS).
    Without this, "Adajan Gam" and "Pal Gam" share a word and would read as
    the same neighbourhood."""
    return [
        word
        for word in _AREA_WORD_RE.findall(text)
        if len(word) >= config.AREA_MIN_WORD_LENGTH and word not in config.AREA_STOP_WORDS
    ]


@lru_cache(maxsize=_LOCATION_CACHE_SIZE)
def _parse_location(written: str) -> Tuple[str, frozenset, Dict[str, List[str]]]:
    """One listing's location text, reduced to the three forms the tiers
    compare against — memoised BY VALUE, which is what keeps this affordable.

    A full rescore compares every client against every listing, so without
    this cache the same listing's address would be lowercased, split and
    bucketed once per client: a few thousand listings times a few hundred
    clients is millions of identical regex passes per nightly run, all
    producing the same answer. Keyed on the text rather than on a property,
    so two listings in the same society share one entry and an edited listing
    simply computes a new one. Bounded, so it can never grow into a leak on a
    small Railway container — the working set is one entry per listing, a few
    hundred kilobytes at this scale."""
    haystack = normalize_token(written)
    words = _significant_words(haystack)
    return haystack, frozenset(words), _by_prefix(words)


def _by_prefix(words: List[str]) -> Dict[str, List[str]]:
    """Words bucketed by their first two letters — the prefilter that keeps
    the fuzzy tier affordable. A full difflib comparison of every client word
    against every word of every listing's address would be tens of millions
    of ratio() calls per nightly run; two words that do not even start alike
    are never close enough to matter, so only one bucket is ever compared."""
    buckets: Dict[str, List[str]] = {}
    for word in words:
        buckets.setdefault(word[:2], []).append(word)
    return buckets


def location_score(
    client_areas: List[str],
    property_area: Optional[str],
    property_address: Optional[str] = None,
    property_society: Optional[str] = None,
) -> Optional[float]:
    """Geographic relevance in [0, 1], best tier wins across every area the
    client named — or None when the client named no area (never scored, never
    a filter) or when the LISTING has no location at all (an unknown, which
    scoring prices as one rather than as a match).

    The society/project name is part of the listing's location text because
    people routinely write one in place of an area ("Black Residency") on
    both sides of this comparison."""
    if not client_areas:
        return None
    written = " ".join(part for part in (property_area, property_address, property_society) if part and part.strip())
    if not written.strip():
        return None
    haystack, hay_set, hay_buckets = _parse_location(written)
    # A client who named nothing but a city HAS stated that city as their
    # area, so a listing in it is an exact hit. A client who named a locality
    # as well has stated the locality — the city alone is then same-city, not
    # the area they asked for.
    city_only = all(area in config.CITY_NAMES for area in client_areas)
    best = config.LOCATION_OTHER
    for area in client_areas:
        best = max(best, _area_tier(area, haystack, hay_set, hay_buckets, city_only))
        if best >= config.LOCATION_EXACT:
            break
    return best


def _area_tier(
    area: str,
    haystack: str,
    hay_set: frozenset,
    hay_buckets: Dict[str, List[str]],
    city_only: bool,
) -> float:
    is_city = area in config.CITY_NAMES
    if area and area in haystack:
        return config.LOCATION_EXACT if (city_only or not is_city) else config.LOCATION_SAME_CITY
    if is_city:
        # The client named this city and the listing does not mention it.
        # Nothing here can say whether it is next door or 400km away, so it
        # gets the honest bottom tier rather than an invented distance.
        return config.LOCATION_OTHER
    words = _significant_words(area)
    if any(word in hay_set for word in words):
        return config.LOCATION_PARTIAL
    for word in words:
        for candidate in hay_buckets.get(word[:2], ()):
            if abs(len(candidate) - len(word)) <= 2 and difflib.SequenceMatcher(None, word, candidate).ratio() >= (
                config.LOCATION_FUZZY_RATIO
            ):
                return config.LOCATION_FUZZY
    for neighbour in config.NEARBY_AREAS.get(area, ()):
        if normalize_token(neighbour) in haystack:
            return config.LOCATION_NEARBY
    return config.LOCATION_OTHER


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


# "2 to 5 BHK", "between 2 and 5 BHK", "2 BHK till 4 BHK" — an inclusive
# span. Only ever consulted for text holding exactly two numbers (see
# parse_bhk_intent), where it replaces what used to be read as the set
# {2, 5}; bhk_score already treated that set as everything from 2 to 5, so
# every score is unchanged — the intent just now says what was meant.
_BHK_SPAN_RE = re.compile(r"(?:\bbetween\s*)?" + _NUM + _UNIT + r"\s*(?:to|till|until|through|and)\s*" + _NUM)


class BhkIntent:
    """Parsed shape of a free-text BHK requirement.

    - "minimum": "minimum 3", "3+", "3 or more" -> N and anything above is fine.
    - "range": any comparison wording — "more than 3" (4, 5, ... fine),
      "less than 3", "up to 3", "3 or less", "more than 2 and less than 5".
      `lower`/`upper` are the bounds (None = open on that side), and each
      `*_inclusive` says whether the bound value itself is fine: "more than 3"
      is lower=3 exclusive, "at least 3" would be lower=3 inclusive.
      "2 to 5 BHK" / "between 2 and 5 BHK" is a range too: 2 through 5.
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
    if len(numbers) == 2 and _BHK_SPAN_RE.search(text):
        low, high = sorted(numbers)
        return BhkIntent("range", [], lower=low, upper=high)
    if len(numbers) >= 2:
        return BhkIntent("set", numbers)
    if _STRICT_WORDS_RE.search(text):
        return BhkIntent("exact_strict", numbers)
    return BhkIntent("exact", numbers)


def bhk_is_strict(client_raw: Optional[str]) -> bool:
    """Whether the client ruled everything else out — "exactly 3 BHK", "only
    3 BHK". The eligibility gate reads this to decide which of its two
    distance limits applies (match_config.BHK_STRICT_MAX_DISTANCE), so
    "exactly 3" rejects the 4 BHK its author explicitly excluded instead of
    merely ranking it lower."""
    intent = parse_bhk_intent(client_raw)
    return intent is not None and intent.kind == "exact_strict"


def bhk_distance(client_raw: Optional[str], property_raw: Optional[str]) -> Optional[float]:
    """How many bedrooms this property is from the NEAREST configuration the
    client would accept — 0.0 when the requirement is satisfied outright
    (including every value inside a stated range or above a stated minimum),
    and None when either side has no usable BHK data.

    Split out of bhk_score below so ELIGIBILITY can be expressed in the unit
    the requirement is actually written in — bedrooms — rather than in
    whatever the decay curve happens to turn that distance into. "A 1 BHK is
    two bedrooms away from a 3 BHK request, and that is too far" is a rule a
    broker can read, check and change; "its score fell under 0.3" is not."""
    intent = parse_bhk_intent(client_raw)
    if intent is None or not property_raw:
        return None
    prop_numbers = [float(n) for n in _BHK_NUM_RE.findall(property_raw.lower())]
    if not prop_numbers:
        return None
    return _intent_distance(prop_numbers[0], intent)


def _intent_distance(p: float, intent: BhkIntent) -> float:
    if intent.kind == "range":
        return _range_distance(p, intent)
    if intent.kind == "minimum":
        target = intent.values[0]
        return 0.0 if p >= target else target - p
    if intent.kind == "set":
        low, high = min(intent.values), max(intent.values)
        if low <= p <= high:
            return 0.0
        return (low - p) if p < low else (p - high)
    return abs(p - intent.values[0])


def bhk_score(client_raw: Optional[str], property_raw: Optional[str]) -> Optional[float]:
    """Soft field score in [0, 1], or None if either side has no usable
    BHK data (not comparable — never scored as a match or a mismatch).

    The distance itself is bhk_distance above; this is only the curve over
    it, so the two can never disagree about how far off a property is."""
    distance = bhk_distance(client_raw, property_raw)
    if distance is None:
        return None
    if distance == 0:
        return 1.0
    return _decay(distance, steep=bhk_is_strict(client_raw))


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
