"""HTTP routes for configuring the area-keyword filter (Stage 1 of the
property pipeline). Thin by design — matching logic lives in
Service/WhatsAppDataFetchingService/area_filter_service.py.
"""

from fastapi import APIRouter

from Model.WhatsAppDataFetchingModel.area_filter import AreaFilterSettings
from Service.WhatsAppDataFetchingService import area_filter_service

router = APIRouter(prefix="/area-filter", tags=["area-filter"])


@router.get("/keywords", response_model=AreaFilterSettings)
def get_area_keywords() -> AreaFilterSettings:
    return AreaFilterSettings(keywords=area_filter_service.get_area_keywords())


@router.put("/keywords", response_model=AreaFilterSettings)
def set_area_keywords(settings: AreaFilterSettings) -> AreaFilterSettings:
    """Fully replaces the configured area keyword list (e.g. Althan,
    Bamroli, Udhna) — matches the same "submit the whole selection" pattern
    used by /whatsapp/monitoring-selection."""
    area_filter_service.set_area_keywords(settings.keywords)
    return AreaFilterSettings(keywords=area_filter_service.get_area_keywords())
