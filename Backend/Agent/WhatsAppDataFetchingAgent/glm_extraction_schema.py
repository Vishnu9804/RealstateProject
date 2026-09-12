from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from Agent.WhatsAppDataFetchingAgent.glm_schema_utils import coerce_bhk_field


class GLMPropertyListing(BaseModel):
    """One distinct property listing extracted from a message. A single
    message can describe more than one property (e.g. a broker listing
    several flats in one text), so a message maps to a LIST of these,
    not to one."""

    property_type: Optional[str] = Field(
        default=None,
        description='e.g. "Flat", "Penthouse", "Row House", "Shop", "Office", "Land/Plot", "Bungalow", "Warehouse". '
        'Give the KIND of property only — "duplex", "simplex" and "triplex" describe the internal layout, not the '
        'type, so a "duplex flat" is a "Flat" and a "duplex penthouse" is a "Penthouse".',
    )
    bhk: Optional[str] = Field(default=None, description='Bedroom configuration as written, e.g. "2 BHK", "1 RK".')
    society_name: Optional[str] = Field(
        default=None,
        description='The specific building/project/society/complex name, e.g. "Black Residency", "Sunrise '
        'Heights" — NOT the general locality. Only set this if a specific named building/project is mentioned.',
    )
    area_name: Optional[str] = Field(
        default=None,
        description='The general locality/area, e.g. "Althan" — not a building name. If the message names the '
        "area directly, copy it as written. If the message only gives an address/road/landmark and NOT an area, "
        "and that address is determined (per SERVICE AREA MATCHING) to fall within one of the client's selected "
        "areas, set this to that selected area's name instead of leaving it null — see the rules below.",
    )
    address: Optional[str] = Field(
        default=None,
        description="Any more specific address/landmark details beyond the area name and society name (e.g. "
        "street, road, or landmark). Keep the original address/road/landmark text here even when it was also "
        "used to fill area_name per the rule above — never drop it just because area_name got populated from it.",
    )
    carpet_area_sqft: Optional[float] = Field(
        default=None,
        description="The property's area as a plain number, only if explicitly stated — extract it regardless "
        'of which area unit the message uses: square feet ("1200 sqft" -> 1200.0), Vaar/Gaj ("500 vaar" -> '
        '500.0), or Vigha ("2 vigha" -> 2.0). Copy the bare number exactly as written for whichever unit is '
        "used — never convert between units, never estimate it from the BHK, and never guess when no area is "
        "stated.",
    )
    carpet_area_unit: Optional[str] = Field(
        default=None,
        description='The unit carpet_area_sqft was written in — REQUIRED whenever carpet_area_sqft is set, '
        'null only when carpet_area_sqft is null. Must be exactly one of: "sqft" (for "sqft", "sq ft", '
        '"sq.ft", "square feet"), "vaar" (for "vaar", "gaj", "sq yard", "square yard"), "vigha" (for '
        '"vigha"). Never guess a unit that is not the one actually written for that number.',
    )
    price_text: Optional[str] = Field(
        default=None,
        description="The TOTAL price (never a per-unit rate — see price_per_unit_text for that), normalized "
        'into compact Indian short-scale notation: "cr" for crore, "L" for lakh, "k" for thousand, e.g. '
        '"1,25,00,000₹" or "1.25 crore" -> "1.25cr"; "45 Lakh" or "Rs.45,00,000/-" -> "45L"; "15000/month" '
        '-> "15k". Strip currency symbols (₹, Rs., INR), commas, and Indian digit grouping — output only '
        "the compact form. Never guess a price that is not written.",
    )
    price_amount_inr: Optional[float] = Field(
        default=None,
        description='The TOTAL price converted to a plain INR number only if unambiguous (e.g. "45 Lakh" -> '
        "4500000). Never guess.",
    )
    price_per_unit_text: Optional[str] = Field(
        default=None,
        description="A PER-UNIT rate — never the total price — only if the message states one explicitly, e.g. "
        '"1L/sq ft", "1L per sq ft", "85000/vaar", "1.2cr/vigha", "2500 per sqft". Normalize it the same way as '
        'price_text (compact "cr"/"L"/"k" notation, unit suffix kept as written, e.g. "1,25,000/vaar" -> '
        '"1.25L/vaar", "2500/sqft" stays "2500/sqft"). Do not fill this from a total price — only from wording '
        "that explicitly names a rate per sq ft / per vaar / per vigha / per unit. Null if no per-unit rate is "
        "stated.",
    )
    price_per_unit_amount_inr: Optional[float] = Field(
        default=None,
        description="The PER-UNIT rate above converted to a plain INR number only if unambiguous (e.g. "
        '"1L/vaar" -> 100000). Never guess, and never derive this by dividing a total price by an area '
        "yourself — that division is done deterministically after extraction, not by you. Leave null unless "
        "the message itself states a per-unit rate.",
    )
    listing_type_reason: Optional[str] = Field(
        default=None,
        description="REQUIRED, written BEFORE listing_type (reason first, verdict second — see RENT VS SALE "
        "CLASSIFICATION). Quote or paraphrase the exact Rent or Sale wording found IN THIS MESSAGE for THIS "
        "property, e.g. \"message says 'bhade pe dena hai'\" or \"message says 'bechna hai'\". If the message "
        "gives no explicit Rent/Sale wording at all, write \"no explicit signal\" instead of inventing one.",
    )
    listing_type: Literal["Sale", "Rent"] = Field(
        default="Sale",
        description="Whether this property is being offered/sought for SALE or for RENT — decided FROM "
        "listing_type_reason above, never independently of it. Set to \"Rent\" only when the reason cites an "
        "actual explicit Rent signal; \"Sale\" otherwise, including whenever the reason says there was no "
        "explicit signal. See the RENT VS SALE CLASSIFICATION rules in the prompt. Defaults to \"Sale\" (fail "
        "open) if the model omits the field.",
    )
    contact_name: Optional[str] = Field(
        default=None, description="A person's name given IN THE MESSAGE TEXT as the contact for this property."
    )
    contact_phone: Optional[str] = Field(
        default=None, description="A phone number given IN THE MESSAGE TEXT for this property."
    )
    description: Optional[str] = Field(
        default=None, description="A short, factual one/two-sentence summary written from the message content only."
    )
    information_check_reason: Optional[str] = Field(
        default=None,
        description="REQUIRED, written BEFORE has_enough_information (reason first, verdict second — see "
        "INFORMATION SUFFICIENCY). Name what this property's OWN text actually gave you, e.g. \"has area "
        "'Vesu', 2 BHK and price 45L\" or \"only the word 'Bungalow' and nothing else at all\".",
    )
    has_enough_information: bool = Field(
        default=True,
        description="FALSE only for a fragment you could extract almost nothing from — see the INFORMATION "
        "SUFFICIENCY rules, which set a deliberately extreme bar. A property missing one, two, or even "
        "several fields is still TRUE: incomplete listings are completely normal and are wanted as they "
        "are. Defaults to True (fail open) if the model omits the field, so a missing signal never sends a "
        "usable property into the review queue.",
    )
    source_excerpt: Optional[str] = Field(
        default=None,
        description="ONLY set this when has_enough_information is false; leave it null otherwise. Copy, "
        "VERBATIM, just the part of the message text that refers to THIS property — the one line/bullet/"
        "fragment it came from, plus any immediately adjacent wording that belongs to it. Never the whole "
        "message (a message can list ten properties, and nine of them are not this one), never another "
        "property's line, and never a summary in your own words. A human reads exactly this text to fill "
        "the property's details in by hand, so copying the wrong part is worse than copying nothing.",
    )
    area_match_reason: Optional[str] = Field(
        default=None,
        description="REQUIRED, written BEFORE in_service_area (reason first, verdict second — see SERVICE AREA "
        "MATCHING). One short sentence citing the specific fact that decided it, e.g. \"VIP Road is a well-known "
        "road inside Vesu\" or \"Adajan is a distinct locality from every selected area\". Ground it in the "
        "area_knowledge recalled at the top of the response rather than restating the area name alone.",
    )
    in_service_area: bool = Field(
        default=True,
        description="True if this property's area/address is inside (or is a known part of / on a road "
        "belonging to) one of the client's selected areas given in the prompt — see the SERVICE AREA MATCHING "
        "rules. False only if it is clearly a different Surat locality not covered by any selected area. When "
        "genuinely unsure, set this to True. Defaults to True (fail open) if the model omits the field, so a "
        "missing signal never causes a wanted property to be dropped.",
    )

    # See glm_schema_utils.coerce_bhk_field: GLM occasionally emits a bare
    # JSON number for "bhk" ("bhk": 2) instead of the string the field above
    # asks for. Coerced to "2 BHK" here, BEFORE pydantic's own str
    # validation runs, so one cosmetic type slip on one property can never
    # fail validation for — and silently discard — the ENTIRE batch
    # response. That would be exactly the "wrong auto-skip silently loses
    # real data" failure this pipeline is built everywhere else to avoid
    # (see property_pipeline_service.py's module docstring), just triggered
    # one layer earlier than duplicate detection.
    _coerce_bhk = field_validator("bhk", mode="before")(coerce_bhk_field)


class GLMPropertyExtraction(BaseModel):
    """The exact JSON shape GLM is asked to return for one input message —
    deliberately limited to fields that actually require language
    understanding to extract. Everything already known for certain from
    WhatsApp itself (sender, group, timestamp) is merged in afterwards by
    Agent/WhatsAppDataFetchingAgent/property_structurer.py, not asked of the LLM.

    Kept one object per message (same order, "source_message_id" matching
    the message id) so a missing/extra response is still easy to detect —
    but each message can now carry MULTIPLE listings in `properties`,
    because one WhatsApp message can advertise more than one property.
    """

    source_message_id: str = Field(description="Must exactly match the message's id as given in the prompt.")
    is_property_listing: bool = Field(
        description="True only if the message OFFERS a specific real-estate property for sale or rent — see "
        "the classification rules in the prompt. False for anything else, including a message ASKING for a "
        "property (that is is_requirement below) and messages that merely discuss or mention a property/area "
        "without actually offering one."
    )
    is_requirement: bool = Field(
        default=False,
        description="True only when the message is a DEMAND — someone asking FOR a property they do not have "
        "(\"I want to look for 3bhk flat in vesu\", \"3BHK joie chhe Vesu ma\", \"need shop on rent in "
        "Althan\") — rather than offering one. Mutually exclusive with is_property_listing: when this is true, "
        "is_property_listing is false and \"properties\" stays EMPTY, because the message is handed to a "
        "separate requirement-extraction stage instead of being stored as a listing. Defaults to False (fail "
        "safe) if the model omits the field, which leaves the message treated exactly as it was before this "
        "field existed — so a missing signal can never re-route a real listing away from the Properties page.",
    )
    property_lines: List[str] = Field(
        default_factory=list,
        description="REQUIRED whenever is_property_listing is true, and written BEFORE \"properties\" — the "
        "same reason-before-verdict discipline as listing_type_reason/area_match_reason. One SHORT entry per "
        "distinct property: just enough of that property's own line to tell it apart from the others, a "
        "handful of words at most (e.g. \"Olive Club Vesu 650sqft\", \"2nd VIP Road 750sqft\"). Keep each "
        "entry brief — this is a checklist to count and identify the properties, not a transcript, and a "
        "long copy of every line only slows the response down. Do this by walking the message top to bottom "
        "and noting every property line you meet, BEFORE extracting any structured fields, filtering nothing "
        "and merging nothing. \"properties\" must then contain exactly one entry per item listed here, in "
        "the same order. Empty list if is_property_listing is false.",
    )
    properties: List[GLMPropertyListing] = Field(
        default_factory=list,
        description="One entry per DISTINCT property mentioned in the message — exactly one per snippet in "
        "\"property_lines\" above, in the same order, same count, no exceptions. Only include more than one "
        "when the message genuinely advertises separate properties — e.g. different society/area, different "
        "BHK, or different price for each one. Do not split a single property's details (like separate rooms/"
        "amenities of the same flat) into multiple entries. Empty list if is_property_listing is false.",
    )
    skip_reason: Optional[str] = Field(
        default=None, description='Why is_property_listing is false, e.g. "question, not a listing", "greeting".'
    )


class GLMExtractionResponse(BaseModel):
    """Top-level shape GLM must return. Z.ai's JSON mode (like OpenAI's)
    requires the model's raw output to be a single JSON *object*, not a
    bare array — so the per-message extractions are wrapped under this one
    key instead of being returned as a top-level list.

    area_knowledge is written FIRST (see the prompt's OUTPUT FORMAT), before
    any extraction — a "generate the knowledge before you use it" step that
    makes the model explicitly recall what it knows about each of the
    client's selected areas (roads, landmarks, well-known societies,
    adjoining micro-localities) before judging any individual property
    against them. This is the fix for a small/fast model otherwise jumping
    straight to a shallow string comparison (deciding "VIP Road" != "Vesu"
    instead of recalling that VIP Road is IN Vesu) — forcing the recall
    into the visible output, ahead of the per-property verdicts, reliably
    improves it without a second API call or a hand-written local
    gazetteer. Purely a reasoning aid: nothing downstream parses it
    programmatically, but it is logged for auditing (see
    property_structurer.py)."""

    area_knowledge: Optional[str] = Field(
        default=None,
        description="Written FIRST, before \"extractions\". For each client-selected area, briefly recall (from "
        "your own knowledge of Surat) prominent roads, landmarks, well-known societies, or micro-localities "
        "commonly understood to be part of it. One short line per selected area. This is general knowledge, not "
        "specific to any message below — do it once for the whole batch.",
    )
    extractions: List[GLMPropertyExtraction] = Field(default_factory=list)
