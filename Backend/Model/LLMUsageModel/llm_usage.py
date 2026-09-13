"""API shapes for the LLM Cost dashboard tab.

Mirrors the snapshot built by Service/LLMUsageService/llm_usage_service.py's
get_overview(). Read-only: nothing in the app writes usage through this API
— each LLM call site records its own usage right where it consumes its
response (see that service module's docstring for exactly where).
"""

from typing import Dict, List

from pydantic import BaseModel


class LLMUsageMetrics(BaseModel):
    """One set of usage numbers — used both for a site's own totals and for
    one model's row underneath it, so the two are always directly
    comparable (same fields, same meaning)."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    avg_input_tokens_per_call: float = 0.0
    avg_output_tokens_per_call: float = 0.0
    avg_total_tokens_per_call: float = 0.0


class LLMUsageModelBreakdown(LLMUsageMetrics):
    """One model's usage under a site — e.g. "glm-4.6" under "property"."""

    model: str


class LLMUsageSite(BaseModel):
    """One LLM call site (property / requirement / intent). `totals` is
    always the sum of `models` — computed that way server-side, never
    stored separately, so the two can never disagree."""

    totals: LLMUsageMetrics
    models: List[LLMUsageModelBreakdown] = []


class LLMUsageOverview(BaseModel):
    sites: Dict[str, LLMUsageSite] = {}
