"""HTTP routes for structured property data — the output of the LLM
structuring stage, and eventually the "Excel-like" dashboard data. Thin by
design; state lives in Service/WhatsAppDataFetchingService/property_pipeline_service.py.
"""

from fastapi import APIRouter

from Model.WhatsAppDataFetchingModel.property_record import PropertyRecord
from Service.WhatsAppDataFetchingService import property_pipeline_service

router = APIRouter(prefix="/properties", tags=["properties"])


@router.get("", response_model=list[PropertyRecord])
def get_properties(limit: int = 100) -> list[PropertyRecord]:
    return property_pipeline_service.get_properties(limit=limit)
