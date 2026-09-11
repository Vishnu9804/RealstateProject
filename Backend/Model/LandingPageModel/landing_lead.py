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
    # Proof that this browser owns `whatsapp_number` — minted by
    # Service/WhatsAppInquiryHandlingService/otp_service.py after the
    # visitor typed back the 4-digit code we sent to it. When it resolves,
    # the number it was minted for REPLACES whatsapp_number, so a lead can
    # no longer be filed against somebody else's phone just by typing
    # theirs. Optional, because a visitor can only be asked for a code when
    # a WhatsApp connection is actually linked to send one from (see that
    # module) — without it this behaves exactly as it always did.
    verification_token: Optional[str] = None


class LandingLeadResult(BaseModel):
    """The answer to a lead submission, and the reason this endpoint no
    longer returns the bare record.

    Two outcomes, and the visitor must be shown a different thing for each:

      - "ok"        -> recorded. `lead` is the stored row.
      - "duplicate" -> this number has already enquired about this exact
                       property, so nothing was written. NOT an error, and
                       not something to apologise for — from their side the
                       enquiry is in, which is precisely what `message`
                       says. See Service/LandingPageService/lead_store.py's
                       has_lead_for_property for why the second one is
                       refused rather than recorded.

    Nothing but the public site calls this endpoint, and it never read the
    returned record — so carrying a status here costs nothing and is the
    only way the page can tell the two endings apart."""

    status: str
    message: Optional[str] = None
    lead: Optional["LandingLeadRecord"] = None


class LandingLeadRecord(BaseModel):
    """A stored lead — see Database/landing_page_models.py's LandingLeadRow
    for what each field means and why property_label is a snapshot."""

    lead_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: str
    whatsapp_number: str
    # The canonical E.164 form of `whatsapp_number` (e.g. "+919876543210"),
    # filled in on the way out of Service/LandingPageService/lead_store.py
    # rather than stored — computing it on read means rows written before
    # this field existed carry it too, and one person who typed their
    # number two different ways ("98765 43210", "+91 98765 43210") still
    # groups into ONE row on the Inquiries page's Property Interest tab.
    # None when the number simply isn't parseable; the raw string stays
    # the record's only truth in that case.
    phone_e164: Optional[str] = None
    property_record_id: Optional[str] = None
    property_label: Optional[str] = None
    created_at: Optional[datetime] = None


# LandingLeadResult above forward-references LandingLeadRecord (defined
# below it, so the two read in the order they matter); this resolves it.
LandingLeadResult.model_rebuild()
