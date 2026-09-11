"""Holds the two customizable "here are the properties" WhatsApp message
templates (broker requirement + client inquiry) — the same
in-memory-hot-path-plus-durable-write pattern as
Service/AgentManagementService/handoff_template_service.py, which this
mirrors deliberately so there is one way templates work in this codebase
rather than two.

In-memory for reads (the send dialogs fetch this on every open); when
DATABASE_URL is set, set_templates also writes through to the database so a
real-estate client's edits survive a restart, and load_from_database
(called once at startup — see main.py) restores them.

The templates stored here are the DEFAULT wording only. Editing a message
inside the send dialog never comes back through this module: that text is
passed straight to the send endpoint, which is exactly the "edit this one
without changing my settings" behaviour the feature asks for.
"""

from __future__ import annotations

from Database import settings_repository
from Database.session import is_database_configured
from Model.PropertySharingModel.property_share_templates import (
    DEFAULT_CLIENT_TEMPLATE,
    DEFAULT_REQUIREMENT_TEMPLATE,
    PropertyShareTemplates,
)

_SETTINGS_KEY = "property_share_message_templates"

_requirement_template: str = DEFAULT_REQUIREMENT_TEMPLATE
_client_template: str = DEFAULT_CLIENT_TEMPLATE


def load_from_database() -> None:
    if not is_database_configured():
        return
    stored = settings_repository.get_value(_SETTINGS_KEY)
    if stored is not None:
        global _requirement_template, _client_template
        _requirement_template = stored.get("requirement_template", _requirement_template)
        _client_template = stored.get("client_template", _client_template)


def get_templates() -> PropertyShareTemplates:
    return PropertyShareTemplates(
        requirement_template=_requirement_template, client_template=_client_template
    )


def set_templates(requirement_template: str, client_template: str) -> PropertyShareTemplates:
    global _requirement_template, _client_template
    _requirement_template = requirement_template
    _client_template = client_template
    if is_database_configured():
        settings_repository.set_value(
            _SETTINGS_KEY,
            {"requirement_template": requirement_template, "client_template": client_template},
        )
    return get_templates()
