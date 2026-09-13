"""HTTP routes for the LLM Cost dashboard tab.

Read-only: nothing writes usage through this API. Each of the three LLM
call sites records its own usage directly (see
Service/LLMUsageService/llm_usage_service.py). Thin by design — all of the
logic lives in that service.
"""

from fastapi import APIRouter

from Model.LLMUsageModel.llm_usage import LLMUsageOverview
from Service.LLMUsageService import llm_usage_service

router = APIRouter(prefix="/llm-usage", tags=["llm-usage"])


@router.get("/overview", response_model=LLMUsageOverview)
def get_overview() -> LLMUsageOverview:
    """One snapshot: for each of the three call sites (property /
    requirement / intent), its totals and its per-model breakdown."""
    return LLMUsageOverview(**llm_usage_service.get_overview())
