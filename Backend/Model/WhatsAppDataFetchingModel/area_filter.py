from typing import List

from pydantic import BaseModel


class AreaFilterSettings(BaseModel):
    """The areas the client has selected as ones they serve (e.g. "Vesu",
    "Althan", "Bamroli"), configured on the Settings page. One of several
    OR-matched signals in the Stage 1 property-relevance filter (Service/
    WhatsAppDataFetchingService/area_filter_service.py), and the list the
    LLM structuring stage matches each extracted property's area/address
    against to decide review_status="outsider" vs. accepted (see Agent/
    WhatsAppDataFetchingAgent/property_structurer.py)."""

    keywords: List[str] = []
    # Response-only: whether this list may be changed right now
    # (ALLOW_AREA_CHANGE in Backend/.env — see Config/settings.py). Ignored
    # when sent in a request body; the server decides.
    editable: bool = False
