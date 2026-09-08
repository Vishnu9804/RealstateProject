"""Holds the two customizable WhatsApp hand-off message templates (agent
brief + client confirmation) — same in-memory-hot-path-plus-durable-write
pattern as Service/WhatsAppDataFetchingService/display_settings_service.py.
In-memory for reads (HandoffDialog.tsx fetches this on every open); when
DATABASE_URL is set, set_templates also writes through to the database so
a real-estate client's edits survive a restart, and load_from_database
(called once at startup — see main.py) restores them.
"""

from __future__ import annotations

from Database import settings_repository
from Database.session import is_database_configured
from Model.AgentManagementModel.handoff_templates import (
    DEFAULT_AGENT_TEMPLATE,
    DEFAULT_CLIENT_TEMPLATE,
    HandoffTemplates,
)

_SETTINGS_KEY = "handoff_message_templates"

_agent_template: str = DEFAULT_AGENT_TEMPLATE
_client_template: str = DEFAULT_CLIENT_TEMPLATE


def load_from_database() -> None:
    if not is_database_configured():
        return
    stored = settings_repository.get_value(_SETTINGS_KEY)
    if stored is not None:
        global _agent_template, _client_template
        _agent_template = stored.get("agent_template", _agent_template)
        _client_template = stored.get("client_template", _client_template)


def get_templates() -> HandoffTemplates:
    return HandoffTemplates(agent_template=_agent_template, client_template=_client_template)


def set_templates(agent_template: str, client_template: str) -> HandoffTemplates:
    global _agent_template, _client_template
    _agent_template = agent_template
    _client_template = client_template
    if is_database_configured():
        settings_repository.set_value(_SETTINGS_KEY, {"agent_template": agent_template, "client_template": client_template})
    return get_templates()
