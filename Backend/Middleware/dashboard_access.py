"""Optional shared-key gate for the Dashboard's read-only usage endpoints
(LLM Cost, Neon DB, Backend, Message to Model).

Those endpoints carry no login: the Dashboard is a separate static page with
no account system of its own. That was harmless while they returned only
counters — but the Message to Model feed returns raw WhatsApp message text
and broker phone numbers, which must not be readable by anyone who finds the
API's public URL once it is hosted.

So: set DASHBOARD_KEY in Backend/.env (and the same value as
VITE_DASHBOARD_KEY when building the Dashboard) and every one of those
routes requires it in an X-Dashboard-Key header. Leave it blank and nothing
changes — local development keeps working with no setup at all.

`async def` on purpose: a plain header comparison has no business costing a
thread-pool hop on every dashboard poll.
"""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Header, HTTPException

from Config.settings import get_settings


async def require_dashboard_key(x_dashboard_key: Optional[str] = Header(default=None)) -> None:
    expected = get_settings().dashboard_key
    if not expected:
        return
    if not x_dashboard_key or not hmac.compare_digest(x_dashboard_key.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(
            status_code=401,
            detail="This dashboard endpoint needs the dashboard access key (X-Dashboard-Key = DASHBOARD_KEY).",
        )
