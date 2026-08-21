"""Generic key-value settings persistence backing the various *_settings
services (Service/area_filter_service.py, Service/display_settings_service.py,
Service/duplicate_detection_service.py) once DATABASE_URL is set.

Each service still owns its own shape and in-memory default — this module
only adds a durable read/write layer underneath, loaded once at startup
(see each service's load_from_database()) rather than hit on every read, so
hot paths like the area-keyword filter (checked on every incoming WhatsApp
message) never pay for a database round-trip.
"""

from __future__ import annotations

from typing import Any, Optional

from Database.models import AppSettingRow
from Database.session import get_session


def get_value(key: str) -> Optional[Any]:
    with get_session() as session:
        row = session.get(AppSettingRow, key)
        return row.value if row else None


def set_value(key: str, value: Any) -> None:
    with get_session() as session:
        row = session.get(AppSettingRow, key)
        if row is None:
            session.add(AppSettingRow(key=key, value=value))
        else:
            row.value = value
