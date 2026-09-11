from datetime import datetime

from Model.WhatsAppDataFetchingModel.property_record import PropertyRecord


class SoldOutPropertyRecord(PropertyRecord):
    """A sold-out property as the API returns it.

    Deliberately the SAME shape as PropertyRecord plus two sale fields,
    rather than a narrower model of its own: the Sold out tab is the
    Properties page's own table, cards, filters, search and detail dialog
    reused as they are (see Frontend/src/pages/DashboardPage.tsx). A
    different shape would have meant a second copy of every one of those,
    kept in step by hand — and they would drift.

    The three Landing Page fields PropertyRecord carries
    (on_landing_page/landing_page_updated_at/qualified_at) keep their
    defaults here and are not stored: they describe whether a property is
    PUBLISHED on the public site, and a sold-out property never is — the
    row it would be published from no longer exists (see
    Database/soldout_property_models.py).
    """

    sold_out_at: datetime
    # Pre-formatted IST, exactly like PropertyRecord.formatted_timestamp and
    # honouring the same 12h/24h display setting — so the frontend never
    # does timezone maths, and the sale date reads identically to every
    # other date in the application.
    formatted_sold_out_at: str
