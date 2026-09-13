"""API shapes for the Dashboard's Neon DB tab.

Mirrors Service/NeonUsageService/neon_usage_service.py's get_overview().
Read-only, and deliberately served entirely from memory — see that module's
docstring for why reading this must not cost CU-hours or transfer.
"""

from typing import List, Optional

from pydantic import BaseModel


class NeonAssumptions(BaseModel):
    """Shown on the tab so the numbers can be checked against the Neon
    console rather than trusted blindly."""

    compute_units: float = 0.25
    autosuspend_seconds: float = 300.0
    tracking_since: Optional[str] = None


class NeonTotals(BaseModel):
    wakeups: int = 0
    queries: int = 0
    cu_hours: float = 0.0
    awake_seconds: float = 0.0
    bytes: int = 0
    bytes_text: str = "0 B"


class NeonOperation(BaseModel):
    """One operation inside a wake-up — or the single folded line standing
    in for every operation too small to deserve its own."""

    label: str
    area: str
    queries: int = 0
    seconds: float = 0.0
    bytes: int = 0
    summary: str


class NeonComputeWindow(BaseModel):
    """One wake-up: a run of queries with no gap long enough for the compute
    to suspend. This — not a single query — is what actually adds CU-hours."""

    at: str
    ended_at: str
    still_awake: bool = False
    trigger: str
    trigger_kind: str
    queries: int = 0
    active_seconds: float = 0.0
    idle_tail_seconds: float = 300.0
    billed_seconds: float = 0.0
    cu_hours: float = 0.0
    share_percent: float = 0.0
    summary: str
    operations: List[NeonOperation] = []


class NeonTransferEntry(BaseModel):
    """One operation's share of Network Transfer, newest first."""

    at: str
    trigger: str
    trigger_kind: str
    label: str
    area: str
    queries: int = 0
    bytes: int = 0
    rows: int = 0
    share_percent: float = 0.0
    summary: str


class NeonUsageOverview(BaseModel):
    assumptions: NeonAssumptions
    totals: NeonTotals
    compute: List[NeonComputeWindow] = []
    transfer: List[NeonTransferEntry] = []
