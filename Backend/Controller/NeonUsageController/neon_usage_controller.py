"""HTTP routes for the Dashboard's Neon DB tab.

Read-only routes, served from in-memory counters — they open no database
connection and run no query, so polling this tab can never add to the
CU-hours and Network Transfer it is reporting on (see
Service/NeonUsageService/neon_usage_service.py's docstring).
"""

from typing import Optional

from fastapi import APIRouter, Response

from Model.NeonUsageModel.neon_usage import NeonUsageOverview
from Service.BackendUsageService import usage_feed
from Service.NeonUsageService import neon_usage_service

router = APIRouter(prefix="/neon-usage", tags=["neon-usage"])


@router.get("/overview", response_model=NeonUsageOverview)
def get_overview() -> NeonUsageOverview:
    """Every wake-up that spent CU-hours and every operation that moved
    data, newest first."""
    return NeonUsageOverview(**neon_usage_service.get_overview())


@router.get("/windows")
def get_windows(cursor: Optional[str] = None) -> Response:
    """Wake-ups of the last 48 hours that changed after `cursor`, each
    already rendered into its CU-hours row and Network Transfer rows (see
    usage_feed for the cursor contract)."""
    return usage_feed.json_response(neon_usage_service.get_changes(cursor))
