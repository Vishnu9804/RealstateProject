"""HTTP routes for configuring the client's selected areas (Settings page).
This list feeds two stages: it's one of the OR-matched signals in the
Stage 1 property-relevance filter, and it's the set of areas the LLM
structuring stage (Stage 3) matches each extracted property against to
decide review_status="outsider" vs. accepted. Thin by design — matching
logic lives in Service/WhatsAppDataFetchingService/area_filter_service.py.

Changing the list is gated by ALLOW_AREA_CHANGE (Config/settings.py). The
gate lives here, on the write itself, so a locked list stays locked even for
a request that never went through the Settings page.
"""

from fastapi import APIRouter, HTTPException

from Config.settings import get_settings
from Model.WhatsAppDataFetchingModel.area_filter import AreaFilterSettings
from Service.WhatsAppDataFetchingService import area_filter_service

router = APIRouter(prefix="/area-filter", tags=["area-filter"])


def _current() -> AreaFilterSettings:
    return AreaFilterSettings(
        keywords=area_filter_service.get_area_keywords(),
        editable=get_settings().allow_area_change,
    )


@router.get("/keywords", response_model=AreaFilterSettings)
def get_area_keywords() -> AreaFilterSettings:
    return _current()


@router.put("/keywords", response_model=AreaFilterSettings)
def set_area_keywords(settings: AreaFilterSettings) -> AreaFilterSettings:
    """Fully replaces the configured area keyword list (e.g. Althan,
    Bamroli, Udhna) — matches the same "submit the whole selection" pattern
    used by /whatsapp/monitoring-selection. Refused with 403, changing
    nothing, while ALLOW_AREA_CHANGE is false."""
    if not get_settings().allow_area_change:
        raise HTTPException(
            status_code=403,
            detail="Changing the selected areas is turned off. Set ALLOW_AREA_CHANGE=true in Backend/.env and restart the backend.",
        )
    area_filter_service.set_area_keywords(settings.keywords)
    return _current()
