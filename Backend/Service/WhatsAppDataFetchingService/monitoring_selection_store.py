"""Persists the last-submitted WhatsApp monitoring selection (which groups
and personal numbers to watch) so it survives a backend restart. Mirrors the
pattern used by area_filter_service.py, but there is no in-memory cache here
the way that module needs one for its per-message hot path — this is only
read once per WhatsApp (re)connect (see whatsapp_service._restore_persisted_selection),
never per-message, so hitting the database there is cheap enough to skip the
extra moving part.

No-ops entirely when no database is configured, exactly like every other
*_settings persistence layer here — monitoring still works for the lifetime
of the process, it just won't survive a restart without a database.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from Database import settings_repository
from Database.session import is_database_configured

_SETTINGS_KEY = "whatsapp_monitoring_selection"


def load() -> Optional[Tuple[List[str], List[str]]]:
    """Returns (group_jids, personal_phone_numbers) last saved via `save`,
    or None if nothing has been saved yet (or no database is configured)."""
    if not is_database_configured():
        return None
    stored = settings_repository.get_value(_SETTINGS_KEY)
    if stored is None:
        return None
    return list(stored.get("group_jids", [])), list(stored.get("personal_phone_numbers", []))


def save(group_jids: List[str], personal_phone_numbers: List[str]) -> None:
    """No-op if no database is configured — the in-memory selection still
    works for the current process, it just won't survive a restart."""
    if not is_database_configured():
        return
    settings_repository.set_value(
        _SETTINGS_KEY,
        {"group_jids": list(group_jids), "personal_phone_numbers": list(personal_phone_numbers)},
    )
