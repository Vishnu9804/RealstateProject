from typing import List

from pydantic import BaseModel


class MonitoringSelectionRequest(BaseModel):
    """Which groups and personal numbers to monitor, submitted from the UI.

    Fully replaces whatever was selected before, so this same endpoint can
    be used both for the initial selection right after pairing and later to
    change what's monitored — no restart required.
    """

    group_jids: List[str] = []
    personal_phone_numbers: List[str] = []
