from typing import Optional

from pydantic import BaseModel, Field


class InquiryClassification(BaseModel):
    """Structured-output shape Gemini returns when classifying one user's
    buffered message batch (Agent/WhatsAppInquiryHandlingAgent/inquiry_classifier.py).

    Deliberately just a bool + a short reason — nothing here asks the model
    to restate the message text or produce a long explanation, which would
    only spend output tokens without adding anything the pipeline needs."""

    is_property_related: bool = Field(
        description="True only if the message(s) express genuine interest in buying, "
        "renting, selling, or otherwise inquiring about a property. False for greetings, "
        "small talk, wrong numbers, spam, or anything unrelated to property."
    )
    reason: Optional[str] = Field(
        default=None,
        description='One short phrase explaining the decision, e.g. "greeting only" or '
        '"asks for a rented villa".',
    )
