"""HTTP routes for the Dashboard's Backend tab (RAM and vCPU capsules).

Both are served from this process's own memory — no database, no disk read.
The RAM snapshot is measured on demand and shared for a few seconds (see
Service/BackendUsageService/memory_usage_service.py); the vCPU feed is a
cursor-based delta (see Service/BackendUsageService/usage_feed.py). Thin by
design — all of the logic lives in those services.
"""

from typing import Optional

from fastapi import APIRouter, Response

from Service.BackendUsageService import cpu_usage_service, memory_usage_service, usage_feed

router = APIRouter(prefix="/backend-usage", tags=["backend-usage"])


@router.get("/memory")
def get_memory() -> Response:
    """What the backend's RAM is made of right now."""
    return usage_feed.json_response(memory_usage_service.get_snapshot())


@router.get("/cpu")
def get_cpu(cursor: Optional[str] = None) -> Response:
    """Hourly CPU entries (per endpoint / job) and per-hour process totals
    changed after `cursor`, last 48 hours."""
    return usage_feed.json_response(cpu_usage_service.get_changes(cursor))
