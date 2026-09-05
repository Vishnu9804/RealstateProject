"""The public landing page's enquiry form: a name and a WhatsApp number.

Two fields, and only two, on purpose — every extra box on a public form is
another reason for a visitor to close the tab. Everything else about the
enquiry (which property, when) is filled in by the server from context the
visitor never has to type.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class LandingLeadRequest(BaseModel):
    """What the browser POSTs. The length bounds are the only validation:
    this is an anonymous public endpoint, so they exist to keep a junk
    submission small, not to police how a real person writes their name or
    number."""

    name: str = Field(min_length=1, max_length=120)
    whatsapp_number: str = Field(min_length=6, max_length=24)
    # Present when the form was submitted from a property page, absent when
    # it came from the home page's Contact section.
    property_record_id: Optional[str] = None


class LandingLeadRecord(BaseModel):
    """A stored lead — see Database/landing_page_models.py's LandingLeadRow
    for what each field means and why property_label is a snapshot."""

    lead_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: str
    whatsapp_number: str
    property_record_id: Optional[str] = None
    property_label: Optional[str] = None
    created_at: Optional[datetime] = None
