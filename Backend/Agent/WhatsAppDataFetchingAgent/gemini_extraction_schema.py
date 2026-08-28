from typing import List, Optional

from pydantic import BaseModel, Field


class GeminiPropertyListing(BaseModel):
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
        default=None, description='The general locality/area mentioned, e.g. "Althan" — not a building name.'
    )
    address: Optional[str] = Field(
        default=None,
        description="Any more specific address/landmark details beyond the area name and society name "
        "(e.g. street, road, or landmark).",
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


class GeminiPropertyExtraction(BaseModel):
    """The exact JSON shape Gemini is asked to return for one input
    message — deliberately limited to fields that actually require
    language understanding to extract. Everything already known for
    certain from WhatsApp itself (sender, group, timestamp) is merged in
    afterwards by Agent/WhatsAppDataFetchingAgent/property_structurer.py, not asked of the LLM.

    Kept one object per message (same order, "source_message_id" matching
    the message id) so a missing/extra response is still easy to detect —
    but each message can now carry MULTIPLE listings in `properties`,
    because one WhatsApp message can advertise more than one property.
    """

    source_message_id: str = Field(description="Must exactly match the message's id as given in the prompt.")
    is_property_listing: bool = Field(
        description="True only if the message contains at least one actual property listing being "
        "offered/advertised, not a question, greeting, or unrelated remark."
    )
    properties: List[GeminiPropertyListing] = Field(
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
