from typing import List, Optional

from pydantic import BaseModel, Field


class GLMPropertyListing(BaseModel):
    """One distinct property listing extracted from a message. A single
    message can describe more than one property (e.g. a broker listing
    several flats in one text), so a message maps to a LIST of these,
    not to one."""

    property_type: Optional[str] = Field(
        default=None, description='e.g. "Flat", "Row House", "Shop", "Office", "Land/Plot", "Bungalow", "Warehouse"'
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
        description="The property's area in square feet as a plain number, only if explicitly stated "
        '(e.g. "1200 sqft" -> 1200.0). Never guess or estimate from BHK.',
    )
    price_text: Optional[str] = Field(
        default=None, description='Price as written/normalized for readability, e.g. "45 Lakh", "1.2 Cr".'
    )
    price_amount_inr: Optional[float] = Field(
        default=None,
        description='Price converted to a plain INR number only if unambiguous (e.g. "45 Lakh" -> 4500000). '
        "Never guess.",
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
        description="True only if the message is a genuine buy/sell/rent real-estate listing or request — "
        "see the classification rules in the prompt. False for anything else, including messages that merely "
        "discuss or mention a property/area without actually offering or seeking one."
    )
    properties: List[GLMPropertyListing] = Field(
        default_factory=list,
        description="One entry per DISTINCT property mentioned in the message. Almost always exactly one entry. "
        "Only include more than one when the message genuinely advertises separate properties — e.g. different "
        "society/area, different BHK, or different price for each one. Do not split a single property's details "
        "(like separate rooms/amenities of the same flat) into multiple entries. Empty list if "
        "is_property_listing is false.",
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
