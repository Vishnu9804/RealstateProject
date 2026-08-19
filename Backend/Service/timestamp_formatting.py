"""Converts a UTC-aware datetime to the Indian-Standard-Time display string
the UI expects: DD/MM/YYYY plus either a 12-hour (with AM/PM) or 24-hour
clock, per the user's chosen preference (Service/display_settings_service).

Deliberately not the LLM's job: timezone/date-format conversion is exact
arithmetic, and an LLM is the wrong tool for exact arithmetic. This module
does it in plain Python instead, working off the datetime WhatsApp itself
reported (whatsapp_client.py's `_safe_timestamp`), not anything the LLM
extracted.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def to_ist(dt: datetime) -> datetime:
    """Converts a timezone-aware datetime to IST. Treats a naive value as
    UTC rather than raising — every datetime produced by this codebase is
    UTC-aware already, but formatting must never crash on an edge case."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(IST)


def format_ist(dt: datetime, use_24_hour_format: bool) -> str:
    """e.g. "19/08/2026, 14:05" (24-hour) or "19/08/2026, 2:05 PM" (12-hour)."""
    ist_dt = to_ist(dt)
    date_part = ist_dt.strftime("%d/%m/%Y")
    if use_24_hour_format:
        time_part = ist_dt.strftime("%H:%M")
    else:
        time_part = ist_dt.strftime("%I:%M %p").lstrip("0")
    return f"{date_part}, {time_part}"
