"""HTTP routes for the UI-configurable display preferences. Thin by
design — state lives in Service/WhatsAppDataFetchingService/display_settings_service.py.
"""

from fastapi import APIRouter

from Model.WhatsAppDataFetchingModel.display_settings import DisplaySettings
from Service.WhatsAppDataFetchingService import display_settings_service

router = APIRouter(prefix="/display-settings", tags=["display-settings"])


@router.get("/time-format", response_model=DisplaySettings)
def get_time_format() -> DisplaySettings:
    return DisplaySettings(use_24_hour_format=display_settings_service.get_use_24_hour_format())


@router.put("/time-format", response_model=DisplaySettings)
def set_time_format(settings: DisplaySettings) -> DisplaySettings:
    display_settings_service.set_use_24_hour_format(settings.use_24_hour_format)
    return DisplaySettings(use_24_hour_format=display_settings_service.get_use_24_hour_format())
