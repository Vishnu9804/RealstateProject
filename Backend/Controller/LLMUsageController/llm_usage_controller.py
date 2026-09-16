"""HTTP routes for the LLM Cost and Message to Model dashboard tabs.

Read-only: nothing writes usage through this API. Each of the three LLM
call sites records its own usage directly (see
Service/LLMUsageService/llm_usage_service.py), and the two structuring
stages log what each message became (message_model_service.py). Thin by
design — all of the logic lives in those services.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Response

from Model.LLMUsageModel.llm_usage import LLMUsageOverview
from Service.BackendUsageService import usage_feed
from Service.LLMUsageService import llm_usage_service, message_model_service

router = APIRouter(prefix="/llm-usage", tags=["llm-usage"])


@router.get("/overview", response_model=LLMUsageOverview)
def get_overview() -> LLMUsageOverview:
    """One snapshot: for each of the three call sites (property /
    requirement / intent), its totals and its per-model breakdown."""
    return LLMUsageOverview(**llm_usage_service.get_overview())


@router.get("/hourly")
def get_hourly(cursor: Optional[str] = None) -> Response:
    """Per-IST-hour, per-site, per-model token buckets changed after
    `cursor`, last 48 hours (see usage_feed for the cursor contract)."""
    return usage_feed.json_response(llm_usage_service.get_hourly_changes(cursor))


@router.post("/hourly/{site}/reset", status_code=204)
def reset_hourly(site: str) -> None:
    """Clears the 48-hour view for one call site (the LLM Cost tab's
    per-tab Clear button). The all-time counters under /overview are a
    separate, permanent cost record and are never touched by this."""
    try:
        llm_usage_service.reset_hourly_for_site(site)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"No such site: {site!r}")


@router.get("/messages")
def get_messages(cursor: Optional[str] = None) -> Response:
    """Message -> model entries logged after `cursor`, last 48 hours."""
    return message_model_service.get_changes(cursor)
