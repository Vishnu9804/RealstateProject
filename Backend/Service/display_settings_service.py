"""Holds UI-configurable display preferences — not secrets, so they don't
belong in Config/settings.py (env-sourced). In-memory for now, matching the
rest of the pipeline's "no DB yet by design" state; the database step will
persist this in a settings table instead.
"""

from __future__ import annotations

_use_24_hour_format: bool = False


def set_use_24_hour_format(value: bool) -> None:
    global _use_24_hour_format
    _use_24_hour_format = value


def get_use_24_hour_format() -> bool:
    return _use_24_hour_format
