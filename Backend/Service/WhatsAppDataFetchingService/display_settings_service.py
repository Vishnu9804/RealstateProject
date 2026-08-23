"""Holds UI-configurable display preferences — not secrets, so they don't
belong in Config/settings.py (env-sourced). In-memory for the hot path
(read on every property formatted for the API); when DATABASE_URL is set,
`set_use_24_hour_format` also writes through to the database so the
preference survives a restart, and `load_from_database` (called once at
startup — see main.py) restores it.
"""

from __future__ import annotations

from Database import settings_repository
from Database.session import is_database_configured

_SETTINGS_KEY = "display_settings"

_use_24_hour_format: bool = False


def load_from_database() -> None:
    if not is_database_configured():
        return
    stored = settings_repository.get_value(_SETTINGS_KEY)
    if stored is not None:
        global _use_24_hour_format
        _use_24_hour_format = bool(stored.get("use_24_hour_format", _use_24_hour_format))


def set_use_24_hour_format(value: bool) -> None:
    global _use_24_hour_format
    _use_24_hour_format = value
    if is_database_configured():
        settings_repository.set_value(_SETTINGS_KEY, {"use_24_hour_format": value})


def get_use_24_hour_format() -> bool:
    return _use_24_hour_format
