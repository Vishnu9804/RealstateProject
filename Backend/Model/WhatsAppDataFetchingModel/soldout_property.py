"""The shape of a property AFTER its deal has closed.

A sold-out property is deliberately NOT a status on `properties` — it is a
row moved out of that table entirely (see Database/soldout_property_models.py
and Service/WhatsAppDataFetchingService/soldout_property_service.py). Once a
deal is done the listing must stop being matched, published, assigned or
edited, and the cheapest way to guarantee all of that at once is for it to
no longer exist where those features look.

The record itself keeps the exact same shape a property has (it subclasses
StructuredProperty field-for-field, message metadata included) purely so the
Sold out view can reuse the Properties page's own table/cards/detail dialog
unchanged — a sold property is still the same property, it just isn't for
sale any more. The one thing added is `sold_out_at`.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from Model.WhatsAppDataFetchingModel.structured_property import StructuredProperty


class SoldOutProperty(StructuredProperty):
    """A property that has been moved into the sold-out table. Carries no
    embedding: nothing scores a sold property (see
    Service/ClientPropertyMatchingService/matching_service.py), so storing a
    vector for it would be dead weight that only invites something to start
    matching against it again by accident."""

    sold_out_at: datetime


class SoldOutPropertyRecord(SoldOutProperty):
    """A SoldOutProperty as returned by the API — the exact same two display
    additions PropertyRecord makes (see Model/WhatsAppDataFetchingModel/
    property_record.py), plus the sold-out moment formatted the same way, so
    the frontend never does timezone/format math for either timestamp."""

    formatted_timestamp: str
    formatted_sold_out_at: str
    # Same story as PropertyRecord.image_count: accurate everywhere, but the
    # list endpoint computes it in SQL without shipping the photo bytes.
    image_count: int = 0


class SoldOutMoveResult(BaseModel):
    """What marking one property sold out actually did — returned so the
    operator sees the consequences rather than just "done".

    `agents_notified` counts agents REACHED on WhatsApp, so a message that
    failed to send is visible instead of being silently reported as
    delivered (same reasoning as the hand-off cancellation's own
    CancelResult in Controller/WhatsAppInquiryHandlingController/
    whatsapp_inquiry_controller.py).
    """

    property: SoldOutPropertyRecord
    # Active site visits called off — one per (agent, client) pair that had
    # this property out. Completed visits are never touched.
    cancelled_visits: int
    agents_notified: int
    agents_failed: int
    # Cached match scores and hand-picks removed, across every client.
    cleared_matches: int
    cleared_manual_picks: int
