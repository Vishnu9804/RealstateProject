"""Persists the multi-number WhatsApp connection roster (which numbers are
linked, what each is used for, and — for the Property role — which of its
groups and personal numbers are selected for Property and, separately, for
Requirement) so all of it survives a backend restart.
Mirrors the pattern used by monitoring_selection_store.py (which this
supersedes), but keyed per connection rather than for a single client.

No-ops entirely when no database is configured, exactly like every other
*_settings/*_selection persistence layer here — connections still work for
the lifetime of the process, they just won't survive a restart without a
database.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from Database import settings_repository
from Database.session import is_database_configured

_SETTINGS_KEY = "whatsapp_connections_v2"


def load() -> Optional[List[Dict[str, Any]]]:
    """Returns the last-saved connection roster, or None if nothing has been
    saved yet (or no database is configured). Each entry:
    {"connection_id": str, "session_db_path": str, "roles": [str, ...],
     "property_group_jids": [str, ...], "property_personal_numbers": [str, ...],
     "requirement_group_jids": [str, ...], "requirement_personal_numbers": [str, ...],
     "phone_number": str | None}

    The two requirement keys are absent from any roster saved before that
    selection existed; the reader (whatsapp_connection_manager.
    _restore_connection) defaults them to empty, which is exactly right —
    an existing install keeps capturing properties as before and simply has
    nothing selected for requirements until the operator picks something.
    """
    if not is_database_configured():
        return None
    stored = settings_repository.get_value(_SETTINGS_KEY)
    if stored is None:
        return None
    return list(stored.get("connections", []))


def save(connections: List[Dict[str, Any]]) -> None:
    """No-op if no database is configured — the in-memory roster still works
    for the current process, it just won't survive a restart."""
    if not is_database_configured():
        return
    settings_repository.set_value(_SETTINGS_KEY, {"connections": connections})
