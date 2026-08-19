from typing import Optional

from pydantic import BaseModel, Field


class GeminiPropertyExtraction(BaseModel):
    """The exact JSON shape Gemini is asked to return for one input
    message — deliberately limited to fields that actually require
    language understanding to extract. Everything already known for
    certain from WhatsApp itself (sender, group, timestamp) is merged in
    afterwards by Agent/property_structurer.py, not asked of the LLM.
    """

    source_message_id: str = Field(description="Must exactly match the message's id as given in the prompt.")
    is_property_listing: bool = Field(
        description="True only if the message is an actual property listing being offered/advertised, "
        "not a question, greeting, or unrelated remark."
    )
    property_type: Optional[str] = Field(
        default=None, description='e.g. "Flat", "Row House", "Shop", "Office", "Land/Plot", "Bungalow", "Warehouse"'
    )
    bhk: Optional[str] = Field(default=None, description='Bedroom configuration as written, e.g. "2 BHK", "1 RK".')
    area_name: Optional[str] = Field(default=None, description="The specific locality/area mentioned.")
    address: Optional[str] = Field(
        default=None, description="Any more specific address/landmark details beyond the area name."
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
    skip_reason: Optional[str] = Field(
        default=None, description='Why is_property_listing is false, e.g. "question, not a listing", "greeting".'
    )
