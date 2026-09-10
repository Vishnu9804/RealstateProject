"""HTTP routes for the internal area knowledge base (Temporary page).

Read-only apart from resetting the counters: the knowledge base itself is
grown by the property pipeline as a side effect of structuring (see
Service/WhatsAppDataFetchingService/area_knowledge_service.py), never
through this API. Thin by design — all of the logic lives in that service.
"""

from fastapi import APIRouter

from Model.WhatsAppDataFetchingModel.area_knowledge import AreaKnowledgeOverview
from Service.WhatsAppDataFetchingService import area_knowledge_service

router = APIRouter(prefix="/area-knowledge", tags=["area-knowledge"])


@router.get("/overview", response_model=AreaKnowledgeOverview)
def get_overview() -> AreaKnowledgeOverview:
    """The whole snapshot in one call — totals, breakdowns, every area with
    its learned place strings, and the recent per-property activity."""
    return AreaKnowledgeOverview(**area_knowledge_service.get_overview())


@router.post("/reset-stats", response_model=AreaKnowledgeOverview)
def reset_stats() -> AreaKnowledgeOverview:
    """Zeroes the analysis counters and clears the activity feed. The learned
    place strings are deliberately KEPT — see the service's reset_stats — so
    what gets measured afterwards is how well today's knowledge base performs
    against new traffic."""
    return AreaKnowledgeOverview(**area_knowledge_service.reset_stats())
