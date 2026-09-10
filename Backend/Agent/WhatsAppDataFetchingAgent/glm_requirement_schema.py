from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from Agent.WhatsAppDataFetchingAgent.glm_schema_utils import coerce_bhk_field


class GLMRequirementItem(BaseModel):
    """One distinct requirement (a demand for a property) extracted from a
    message. A single message can carry more than one — a broker forwarding
    three clients' requirements in one text — so a message maps to a LIST of
    these, not to one. Mirrors GLMPropertyListing's shape and discipline
    (reason-before-verdict fields, "never guess" wording) on purpose: the
    same model does both jobs, and consistency between the two prompts is
    what keeps its behaviour predictable."""

    requirement_type: Optional[str] = Field(
        default=None,
        description='The KIND of property being asked for, e.g. "Flat", "Penthouse", "Row House", "Shop", '
        '"Office", "Land/Plot", "Bungalow", "Warehouse". "duplex", "simplex" and "triplex" describe the '
        'internal layout, not the type, so a "duplex flat" is a "Flat". Null if the message does not say.',
    )
    bhk: Optional[str] = Field(
        default=None, description='Bedroom configuration asked for, as written, e.g. "2 BHK", "1 RK", "3-4 BHK".'
    )
    preferred_areas: List[str] = Field(
        default_factory=list,
        description="Every locality/area named as acceptable for THIS requirement, copied EXACTLY as written in "
        'the message, e.g. ["Vesu", "Althan", "Pal"]. Do NOT normalise, translate, correct or expand them, and '
        "do NOT add areas the message never named. Empty list if no area is stated.",
    )
    society_name: Optional[str] = Field(
        default=None,
        description='A specific building/project/society/complex asked for by name, e.g. "Black Residency" — '
        "NOT a general locality (those go in preferred_areas). Null unless a named building is actually asked for.",
    )
    address: Optional[str] = Field(
        default=None,
        description="Any further location detail that is not a locality name on its own — a road, a landmark, "
        '"near X", "on VIP Road". Null if none.',
    )
    carpet_area_min: Optional[float] = Field(
        default=None,
        description='The SMALLEST acceptable size as a plain number, only if stated. A range "1000-1200 sqft" '
        '-> 1000.0; an open-ended "1500+ sqft" or "minimum 1500 sqft" -> 1500.0; an exact "1200 sqft" -> '
        "1200.0 (set BOTH min and max to it). Copy the bare number for whichever unit is used — never convert "
        "between units, never estimate it from the BHK, never guess when no size is stated.",
    )
    carpet_area_max: Optional[float] = Field(
        default=None,
        description='The LARGEST acceptable size, same rules as carpet_area_min. A range "1000-1200 sqft" -> '
        '1200.0; an exact "1200 sqft" -> 1200.0; an open-ended "1500+ sqft" -> null (there is no upper '
        'bound); "up to 1200 sqft" -> 1200.0 with carpet_area_min null.',
    )
    carpet_area_unit: Optional[str] = Field(
        default=None,
        description='The unit the size above was written in — REQUIRED whenever either size is set, null only '
        'when both are null. Exactly one of: "sqft" (for "sqft", "sq ft", "sq.ft", "square feet"), "vaar" (for '
        '"vaar", "gaj", "sq yard", "square yard"), "vigha" (for "vigha"). Never guess a unit that is not the '
        "one actually written.",
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
    furnishing: Optional[str] = Field(
        default=None,
        description='Furnishing asked for, only if stated — exactly one of "Furnished", "Semi-furnished", '
        '"Unfurnished". Null otherwise.',
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
        default=None, description="A person's name given IN THE MESSAGE TEXT as the contact for this requirement."
    )
    contact_phone: Optional[str] = Field(
        default=None, description="A phone number given IN THE MESSAGE TEXT for this requirement."
    )
    description: Optional[str] = Field(
        default=None,
        description="A short, factual one/two-sentence summary of what this person is looking for, written from "
        "the message content only.",
    )

    # See glm_schema_utils.coerce_bhk_field: GLM occasionally emits a bare
    # JSON number for "bhk" ("bhk": 2) instead of the string the field above
    # asks for. Coerced to "2 BHK" here, BEFORE pydantic's own str
    # validation runs, so one cosmetic type slip on one requirement can
    # never fail validation for — and silently discard — the entire batch
    # response (which is what a bare ValidationError otherwise did: see
    # requirement_structurer._parse_extractions).
    _coerce_bhk = field_validator("bhk", mode="before")(coerce_bhk_field)


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
    requirement_lines: List[str] = Field(
        default_factory=list,
        description='REQUIRED whenever is_requirement is true, and written BEFORE "requirements" — the same '
        "reason-before-verdict discipline as listing_type_reason. One SHORT entry per distinct requirement: "
        "just enough of that requirement's own line to tell it apart from the others, a handful of words at "
        'most (e.g. "3BHK Vesu 80L", "Shop VIP Road rent"). Walk the message top to bottom and note every '
        "requirement line you meet BEFORE extracting any structured fields, filtering nothing and merging "
        'nothing. "requirements" must then contain exactly one entry per item listed here, in the same order. '
        "Empty list if is_requirement is false.",
    )
    requirements: List[GLMRequirementItem] = Field(
        default_factory=list,
        description="One entry per DISTINCT requirement in the message — exactly one per snippet in "
        '"requirement_lines" above, in the same order, same count, no exceptions. Only include more than one '
        "when the message genuinely carries separate requirements (different BHK, different area, different "
        "budget, different client). Do not split one requirement's details across multiple entries. Empty list "
        "if is_requirement is false.",
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
