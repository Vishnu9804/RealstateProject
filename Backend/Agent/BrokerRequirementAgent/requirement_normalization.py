"""Deterministic clean-up of the fields a broker requirement is FILTERED and
MATCHED on by name — property type and BHK — so the values the LLM
returns (and the values already stored from before this module existed)
land in one small, predictable vocabulary.

Why it matters: the Broker Requirements page builds its Type/BHK filters
out of the distinct values it sees, and requirement matching's
property-type gate (Service/ClientPropertyMatchingService/normalization.py)
compares these words against property types. "2 BHK FULL FURNISHED 3 BHK
FULL FURNISHED CHALE ROW HOUSE" in the BHK column is neither filterable nor
comparable; "2 BHK, 3 BHK" is both.

Everything here is plain regex/lookup and never invents information: a
value is only ever reformatted, or — when the LLM left a field empty —
recovered from words that are literally present in the requirement's own
text. Pure functions with no project imports, so the structuring stage
(requirement_structurer.py) and the one-time clean-up of stored rows
(Database/broker_requirement_repository.normalize_existing_requirements)
apply exactly the same rules.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

# The names a requirement's type is written with — the same words the
# property side uses for property_type, so a Type filter and a type gate
# read identically on both sides.
REQUIREMENT_TYPES = (
    "Flat",
    "Penthouse",
    "Bungalow",
    "Villa",
    "Row House",
    "Duplex",
    "Plot",
    "Industrial Plot",
    "Land",
    "Shop",
    "Office",
    "Showroom",
    "Warehouse",
    "Commercial Space",
)

_TYPE_SYNONYMS: Dict[str, str] = {
    "flat": "Flat",
    "flats": "Flat",
    "apartment": "Flat",
    "apartments": "Flat",
    "appartment": "Flat",
    "penthouse": "Penthouse",
    "penthouses": "Penthouse",
    "pent house": "Penthouse",
    "bungalow": "Bungalow",
    "bungalows": "Bungalow",
    "bunglow": "Bungalow",
    "bunglows": "Bungalow",
    "banglow": "Bungalow",
    "banglo": "Bungalow",
    "bunglo": "Bungalow",
    "villa": "Villa",
    "villas": "Villa",
    "row house": "Row House",
    "row houses": "Row House",
    "rowhouse": "Row House",
    "rowhouses": "Row House",
    "duplex": "Duplex",
    "plot": "Plot",
    "plots": "Plot",
    "residential plot": "Plot",
    "open plot": "Plot",
    "na plot": "Plot",
    "industrial plot": "Industrial Plot",
    "land": "Land",
    "farmland": "Land",
    "farm land": "Land",
    "agricultural land": "Land",
    "jamin": "Land",
    "jameen": "Land",
    "zameen": "Land",
    "shop": "Shop",
    "shops": "Shop",
    "dukan": "Shop",
    "dukaan": "Shop",
    "office": "Office",
    "offices": "Office",
    "showroom": "Showroom",
    "showrooms": "Showroom",
    "warehouse": "Warehouse",
    "warehouses": "Warehouse",
    "godown": "Warehouse",
    "godowns": "Warehouse",
    "godam": "Warehouse",
    "commercial space": "Commercial Space",
}

# Longest-first, so "industrial plot" wins over "plot" and "row house" is one
# match rather than none; spaces also accept a hyphen ("row-house").
_TYPE_WORD_RE = re.compile(
    r"(?<![a-z0-9])(?:"
    + "|".join(
        re.escape(word).replace(r"\ ", r"[\s\-]*") for word in sorted(_TYPE_SYNONYMS, key=len, reverse=True)
    )
    + r")(?![a-z0-9])",
    re.IGNORECASE,
)
# "Land/Plot" is one type written two ways, not two types.
_LAND_PLOT_RE = re.compile(r"\b(?:land\s*/\s*plot|plot\s*/\s*land)\b", re.IGNORECASE)
_TYPE_SPLIT_RE = re.compile(r",|/|;|&|\+|\bor\b|\band\b", re.IGNORECASE)
# "no shop", "not office" — a type named in order to rule it out.
_NEGATED_BEFORE_RE = re.compile(r"\b(?:no|not|without|except)\s+$", re.IGNORECASE)
# "office job", "office going" describe the tenant, not the property.
_OFFICE_AS_JOB_RE = re.compile(r"^\s+(?:job|jobs|work|staff|goer|goers|going|employee|employees)\b", re.IGNORECASE)
# "row house chale" / "bungalow bhi chalega" — an ALSO-acceptable alternative.
_ALSO_ACCEPTABLE_RE = re.compile(r"\b(?:chale|chalse|chalshe|chalega|chalegi|bhi|also|ok)\b", re.IGNORECASE)
_HOUSE_TYPES = frozenset({"Bungalow", "Villa", "Row House", "Duplex"})
_FLAT_TYPES = frozenset({"Flat", "Penthouse"})

_MAX_BEDROOMS = 10
_BHK_GROUP_RE = re.compile(
    r"(\d+(?:\.\d+)?(?:\s*(?:/|,|&|-|to|or|and)\s*\d+(?:\.\d+)?)*)\s*(\+)?\s*(bhk|b\.h\.k\.?|rk)(?![a-z])",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_RANGE_SEP_RE = re.compile(r"\d\s*(?:-|to)\s*\d", re.IGNORECASE)


# --- property type ---------------------------------------------------------


def _types_in(text: str) -> List[str]:
    """Every canonical type literally named in `text`, in order of first
    appearance — skipping one that is negated ("no shop") or that is really
    a job description ("office job")."""
    found: List[str] = []
    for match in _TYPE_WORD_RE.finditer(text):
        if _NEGATED_BEFORE_RE.search(text[max(0, match.start() - 12) : match.start()]):
            continue
        key = " ".join(match.group(0).lower().replace("-", " ").split())
        canonical = _TYPE_SYNONYMS.get(key) or _TYPE_SYNONYMS.get(key.replace(" ", ""))
        if canonical is None:
            continue
        if canonical == "Office" and _OFFICE_AS_JOB_RE.match(text[match.end() : match.end() + 16]):
            continue
        if canonical not in found:
            found.append(canonical)
    return found


def _join_types(types: List[str]) -> Optional[str]:
    unique: List[str] = []
    for value in types:
        if value and value not in unique:
            unique.append(value)
    # "Duplex" is a layout, not a type, whenever a real type sits next to it
    # ("duplex flat", "duplex penthouse") — same rule the matching side's
    # normalization.canonical_type_token applies.
    if "Duplex" in unique and len(unique) > 1:
        unique.remove("Duplex")
    return ", ".join(unique) or None


def canonical_requirement_type(raw: Optional[str]) -> Optional[str]:
    """The LLM's (or a stored) type rewritten into REQUIREMENT_TYPES names,
    several joined with ", " main-first: "flat / row house" -> "Flat, Row
    House", "Land/Plot" -> "Plot", "Bunglow" -> "Bungalow". A part that names
    no known type is kept in title case rather than dropped — unless it holds
    digits ("2 BHK" is not a type) — so an unusual but genuine type is never
    lost."""
    if not raw or not raw.strip():
        return None
    text = _LAND_PLOT_RE.sub("plot", raw)
    collected: List[str] = []
    for part in _TYPE_SPLIT_RE.split(text):
        part = " ".join(part.split())
        if not part:
            continue
        named = _types_in(part)
        if named:
            collected.extend(named)
        elif not any(character.isdigit() for character in part) and len(part) <= 30:
            collected.append(part.title())
    return _join_types(collected)


def infer_requirement_type(text: Optional[str], has_bhk: bool) -> Optional[str]:
    """Recovers a type the LLM left empty, from type words literally present
    in the requirement's own text. None when the text names none — a bare
    "2 BHK" stays untyped rather than being guessed into a Flat.

    The one addition: a BHK request whose only named types are houses AND
    that marks them as an alternative ("2 BHK / 3 BHK full furnished, row
    house chale") is asking for a flat first, with the house also
    acceptable — so "Flat" is put in front."""
    if not text or not text.strip():
        return None
    lowered = _LAND_PLOT_RE.sub("plot", text)
    found = _types_in(lowered)
    if not found:
        return None
    if has_bhk and all(value in _HOUSE_TYPES for value in found) and _ALSO_ACCEPTABLE_RE.search(lowered):
        found = ["Flat", *found]
    return _join_types(found)


# --- BHK -------------------------------------------------------------------


def _format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _bhk_tokens(text: str) -> List[str]:
    rk: List[float] = []
    bhk: List[float] = []
    plus_values: set = set()
    for match in _BHK_GROUP_RE.finditer(text):
        group, plus, unit = match.group(1), match.group(2), match.group(3).lower()
        numbers = [float(number) for number in _NUMBER_RE.findall(group)]
        if (
            len(numbers) == 2
            and _RANGE_SEP_RE.search(group)
            and all(number.is_integer() for number in numbers)
            and 0 < numbers[1] - numbers[0] <= 3
        ):
            numbers = [float(value) for value in range(int(numbers[0]), int(numbers[1]) + 1)]
        target = rk if unit == "rk" else bhk
        for number in numbers:
            if 0 < number <= _MAX_BEDROOMS and number not in target:
                target.append(number)
        if plus and len(numbers) == 1 and unit != "rk":
            plus_values.add(numbers[0])
    tokens = [f"{_format_number(value)} RK" for value in sorted(rk)]
    tokens += [f"{_format_number(value)}{'+' if value in plus_values else ''} BHK" for value in sorted(bhk)]
    return tokens


def canonical_bhk(raw: Optional[str]) -> Optional[str]:
    """Only the bedroom configurations, as "N BHK" / "N RK", several joined
    with ", " smallest first: "4bhk , 5bhk" -> "4 BHK, 5 BHK", "1/2 BHK" ->
    "1 BHK, 2 BHK", "3-4 BHK" -> "3 BHK, 4 BHK", "3+ BHK" kept as "3+ BHK",
    a bare "3" -> "3 BHK". Everything that is not a configuration (furnishing,
    type words) is dropped. Text with no configuration at all is returned
    unchanged rather than blanked — an odd value a human typed stays theirs."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    tokens = _bhk_tokens(text)
    if tokens:
        return ", ".join(tokens)
    if _NUMBER_RE.search(text) and not re.search(r"[a-z]", text, re.IGNORECASE):
        tokens = _bhk_tokens(f"{text} bhk")
        if tokens:
            return ", ".join(tokens)
    return text


def infer_bhk(text: Optional[str]) -> Optional[str]:
    """Recovers configurations the LLM left empty, from "N BHK"/"N RK"
    literally written in the given text. None when there are none."""
    if not text:
        return None
    tokens = _bhk_tokens(text)
    return ", ".join(tokens) if tokens else None
