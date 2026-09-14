"""HTTP routes for the Dashboard's Neon DB tab.

One read-only route, served from in-memory counters — it opens no database
connection and runs no query, so polling this tab can never add to the
CU-hours and Network Transfer it is reporting on (see
Service/NeonUsageService/neon_usage_service.py's docstring).
"""

from fastapi import APIRouter

from Model.NeonUsageModel.neon_usage import NeonUsageOverview
from Service.NeonUsageService import neon_usage_service

router = APIRouter(prefix="/neon-usage", tags=["neon-usage"])


@router.get("/overview", response_model=NeonUsageOverview)
def get_overview() -> NeonUsageOverview:
    """Every wake-up that spent CU-hours and every operation that moved
    data, newest first."""
    return NeonUsageOverview(**neon_usage_service.get_overview())
