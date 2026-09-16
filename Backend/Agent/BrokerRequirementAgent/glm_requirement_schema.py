from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from Agent.WhatsAppDataFetchingAgent.glm_schema_utils import coerce_bhk_field


def _coerce_text_field(value: Any) -> Any:
    """Same idea as glm_schema_utils.coerce_bhk_field, for the free-text
    fields below: GLM sometimes answers a text field with a number or a list
    ("requirement_type": ["Flat", "Row House"]). Left alone, that one slip
    fails validation for the WHOLE batch response and silently discards
    every requirement in it. A list is joined, a number is stringified, a
    stray boolean is treated as "not stated"; anything else passes through
    to pydantic unchanged."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        joined = ", ".join(str(item).strip() for item in value if item is not None and str(item).strip())
        return joined or None
    return value


def _coerce_list_field(value: Any) -> Any:
    """A single area answered as a bare string instead of a one-item list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return value


class GLMRequirementItem(BaseModel):
    """One distinct requirement (a demand for a property) extracted from a
    message. A single message can carry more than one — a broker forwarding
    three clients' requirements in one text — so a message maps to a LIST of
    these, not to one. Mirrors GLMPropertyListing's shape and discipline
    (reason-before-verdict fields, "never guess" wording) on purpose: the
    same model does both jobs, and consistency between the two prompts is
    what keeps its behaviour predictable.

    Deliberately only the fields something downstream actually USES —
    matching (type, BHK, areas, budget, buy/rent, society), sharing (contact
    name, budget, areas) or the budget fallback (budget_text). Every other
    detail a broker writes (furnishing, size, road/landmark, who it is for,
    food, possession, urgency, token, "vaya") is kept, in the broker's own
    words, in `description`."""

    requirement_type: Optional[str] = Field(
        default=None,
        description='The KIND of property asked for, using EXACTLY these names: "Flat", "Penthouse", "Bungalow", '
        '"Villa", "Row House", "Duplex", "Plot", "Industrial Plot", "Land", "Shop", "Office", "Showroom", '
        '"Warehouse", "Commercial Space". flat/apartment -> "Flat"; bungalow/bunglow/banglo -> "Bungalow"; '
        'rowhouse -> "Row House"; residential/open/NA plot -> "Plot"; dukan -> "Shop"; godown -> "Warehouse". A '
        '"duplex flat" is a "Flat". Several acceptable types -> comma-separated, main one first ("Flat, Row '
        'House"). Null if this requirement names no type at all — never guess one.',
    )
    bhk: Optional[str] = Field(
        default=None,
        description='ONLY the bedroom configuration(s) asked for, as "N BHK" / "N RK", several joined with ", ": '
        '"2bhk" -> "2 BHK"; "1/2 BHK" -> "1 BHK, 2 BHK"; "4bhk , 5bhk" -> "4 BHK, 5 BHK"; "3+ BHK" -> "3+ BHK". '
        "Never put furnishing, type or any other word here. Null if not stated.",
    )
    preferred_areas: List[str] = Field(
        default_factory=list,
        description="Every locality/area named as acceptable for THIS requirement, one entry per locality, each "
        'copied EXACTLY as written, e.g. ["Vesu", "Althan", "Pal"]. Localities run together with only spaces, '
        '"•", "/" or "and" between them are still separate entries; multi-word names stay together ("Bhesan '
        'Road", "City Light", "Ghod Dod Road", "Parle Point"). A label word ("area", "aria", "location") is not a '
        "locality. Do NOT normalise, translate, correct or expand them, and do NOT add areas the message never "
        "named. Empty list if no area is stated.",
    )
    society_name: Optional[str] = Field(
        default=None,
        description='A specific building/project/society/complex asked for by name, e.g. "Black Residency" — '
        "NOT a general locality (those go in preferred_areas). Null unless a named building is actually asked for.",
    )
    budget_text: Optional[str] = Field(
        default=None,
        description="The budget EXACTLY as the message expresses it, normalized into compact Indian short-scale "
        'notation: "cr" for crore, "L" for lakh, "k" for thousand. "45 Lakh" -> "45L"; "Rs.45,00,000/-" -> '
        '"45L"; "1.25 crore" -> "1.25cr"; "15000/month" -> "15k"; a range "40 to 50 lakh" -> "40L-50L". Strip '
        "currency symbols, commas and Indian digit grouping. Null if no budget is stated — never guess one.",
    )
    budget_min_inr: Optional[float] = Field(
        default=None,
        description='The LOWER end of the budget as a plain INR number, only if unambiguous ("40 to 50 lakh" -> '
        '4000000; a single "45 lakh" -> 4500000; "under 50L" -> null since only a ceiling is given). Never guess.',
    )
    budget_max_inr: Optional[float] = Field(
        default=None,
        description='The UPPER end of the budget as a plain INR number, only if unambiguous ("40 to 50 lakh" -> '
        '5000000; a single "45 lakh" -> 4500000; "50L and above" -> null since only a floor is given). Never guess.',
    )
    listing_type_reason: Optional[str] = Field(
        default=None,
        description="REQUIRED, written BEFORE listing_type (reason first, verdict second). Quote or paraphrase "
        "the exact Rent or Buy wording found IN THIS MESSAGE for THIS requirement, e.g. \"message says 'rent pe "
        "joie chhe'\" or \"message says 'kharidvu chhe'\". If the message gives no explicit Rent/Buy wording at "
        'all, write "no explicit signal" instead of inventing one.',
    )
    listing_type: Literal["Sale", "Rent"] = Field(
        default="Sale",
        description='Whether this person wants to BUY ("Sale") or to RENT ("Rent") — decided FROM '
        'listing_type_reason above, never independently of it. Set "Rent" only when the reason cites an actual '
        'explicit rental signal; "Sale" otherwise, including whenever the reason says there was no explicit '
        'signal. Defaults to "Sale" (fail open) if the field is omitted.',
    )
    contact_name: Optional[str] = Field(
        default=None,
        description="The contact person's name given IN THE MESSAGE TEXT for this requirement. A contact block at "
        "the end of a multi-requirement message belongs to every requirement in it. Several people -> names "
        'joined with " / "; a firm name goes in brackets after the person, e.g. "Amrutbhai Joshi (Rajeshwar '
        'Properties)".',
    )
    contact_phone: Optional[str] = Field(
        default=None,
        description='The phone number(s) given IN THE MESSAGE TEXT for this requirement, several joined with ", " '
        "in the same order as the names.",
    )
    description: Optional[str] = Field(
        default=None,
        description="A short, factual summary of what this person is looking for, written from the message content "
        "only, that ALSO carries every other detail this requirement states which has no field of its own, in "
        'the message\'s own words: furnishing ("fully furnished", "naked"), size ("500 vaar", "1200 sqft"), '
        'location detail (a road, landmark, "near X"), who it is for ("veg business family", "company '
        'bachelor"), food preference ("pure veg"), possession time ("1-15 Sep"), urgency ("urgent"), "token '
        'ready", how the deal must come ("direct party", "1 vaya"), society age, photos/videos wanted, parking, '
        "floor. Never drop one of these details and never invent one.",
    )

    # See glm_schema_utils.coerce_bhk_field: GLM occasionally emits a bare
    # JSON number for "bhk" ("bhk": 2) instead of the string the field above
    # asks for. Coerced to "2 BHK" here, BEFORE pydantic's own str
    # validation runs, so one cosmetic type slip on one requirement can
    # never fail validation for — and silently discard — the entire batch
    # response (which is what a bare ValidationError otherwise did: see
    # requirement_structurer._parse_extractions).
    _coerce_bhk = field_validator("bhk", mode="before")(coerce_bhk_field)
    _coerce_text = field_validator(
        "requirement_type", "contact_name", "contact_phone", "description", mode="before"
    )(_coerce_text_field)
    _coerce_areas = field_validator("preferred_areas", mode="before")(_coerce_list_field)


class GLMRequirementExtraction(BaseModel):
    """The exact JSON shape the model is asked to return for one input
    message. One object per message (same order, "source_message_id"
    matching the message id) so a missing/extra response is easy to detect
    — but each message can carry MULTIPLE requirements in `requirements`."""

    source_message_id: str = Field(description="Must exactly match the message's id as given in the prompt.")
    is_requirement: bool = Field(
        description="True only if the message is someone genuinely LOOKING FOR / ASKING FOR a property (to buy "
        "or to rent) — a demand, not an offer. False for a property being offered/advertised for sale or rent, "
        "and false for anything that is not about wanting a property at all."
    )
    is_property_listing: bool = Field(
        default=False,
        description="True only when the message is an OFFER — someone presenting a specific property (or several) "
        "for sale or for rent, with its details such as BHK, society, area, size or price (\"2 BHK Flat For RENT, "
        "Orchid Fantasia, Jahangirabad, Rent 20k\") — rather than asking for one. Mutually exclusive with "
        "is_requirement: when this is true, is_requirement is false and \"requirements\" stays EMPTY, because the "
        "message is handed to a separate listing-extraction stage instead. Defaults to False (fail safe) if the "
        "model omits the field, which leaves the message treated exactly as it was before this field existed — so "
        "a missing signal can never re-route a real requirement away from the Broker Requirements page.",
    )
    requirement_lines: List[str] = Field(
        default_factory=list,
        description='REQUIRED whenever is_requirement is true, and written BEFORE "requirements" — the same '
        "reason-before-verdict discipline as listing_type_reason. One SHORT entry per distinct requirement: "
        "just enough of that requirement's own line to tell it apart from the others, a handful of words at "
        "most, including its property type word and BHK when it states them (e.g. \"2 BHK flat Pal Adajan 21k\", "
        '"Bungalow Vesu 3cr"). Walk the message top to bottom and note every requirement you meet BEFORE '
        'extracting any structured fields, filtering nothing and merging nothing. "requirements" must then '
        "contain exactly one entry per item listed here, in the same order. Empty list if is_requirement is false.",
    )
    requirements: List[GLMRequirementItem] = Field(
        default_factory=list,
        description="One entry per DISTINCT requirement in the message — exactly one per snippet in "
        '"requirement_lines" above, in the same order, same count, no exceptions. Separate numbered/bulleted/'
        "emoji blocks are separate requirements; alternatives inside ONE block that share one area list and one "
        'budget ("1/2 BHK", "4bhk, 5bhk") are ONE requirement. Do not split one requirement\'s details across '
        "multiple entries. Empty list if is_requirement is false.",
    )
    skip_reason: Optional[str] = Field(
        default=None,
        description='Why is_requirement is false, e.g. "this is a property being offered, not asked for", '
        '"greeting", "question about paperwork".',
    )


class GLMRequirementResponse(BaseModel):
    """Top-level shape the model must return. Z.ai's JSON mode (like
    OpenAI's) requires the raw output to be a single JSON *object*, not a
    bare array — so the per-message extractions are wrapped under this one
    key.

    Unlike the property response there is deliberately NO area_knowledge
    field here: a requirement is never matched against the client's selected
    areas (see requirement_structurer.py's prompt), so the whole area-recall
    step that exists for properties has nothing to do here and is not paid
    for."""

    extractions: List[GLMRequirementExtraction] = Field(default_factory=list)
