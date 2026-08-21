"""HTTP routes for tuning the duplicate-detection thresholds. Thin by
design — decision logic lives in Service/duplicate_detection_service.py.
"""

from fastapi import APIRouter

from Model.duplicate_detection_settings import DuplicateDetectionSettings
from Service import duplicate_detection_service

router = APIRouter(prefix="/duplicate-detection", tags=["duplicate-detection"])


@router.get("/settings", response_model=DuplicateDetectionSettings)
def get_settings() -> DuplicateDetectionSettings:
    return duplicate_detection_service.get_settings()


@router.put("/settings", response_model=DuplicateDetectionSettings)
def set_settings(settings: DuplicateDetectionSettings) -> DuplicateDetectionSettings:
    duplicate_detection_service.set_settings(settings)
    return duplicate_detection_service.get_settings()
