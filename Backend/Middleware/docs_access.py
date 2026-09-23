"""Optional HTTP Basic Auth gate for the interactive API docs (/docs, /redoc,
/openapi.json — see their route definitions in main.py).

Those pages have nothing to do with login accounts: they are a developer
tool, not a route anyone using the app is meant to open. That was harmless
while the API only existed on localhost — but once this backend is hosted
somewhere public (Railway), the default FastAPI docs are wide open with no
login at all, and they list every endpoint, every request/response field, and
let a visitor fire requests straight from the browser.

So: set DOCS_USERNAME and DOCS_PASSWORD in Backend/.env and every one of
those three routes asks the browser for that username/password (a native
browser prompt, since this uses the WWW-Authenticate: Basic challenge — no
frontend of its own needed). Leave either blank and nothing changes: local
development keeps working with no setup, same convention as
Middleware/dashboard_access.py's DASHBOARD_KEY.
"""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from Config.settings import get_settings

_security = HTTPBasic(auto_error=False)


def require_docs_auth(credentials: Optional[HTTPBasicCredentials] = Depends(_security)) -> None:
    settings = get_settings()
    expected_username = settings.docs_username
    expected_password = settings.docs_password
    if not expected_username or not expected_password:
        return
    valid = bool(credentials) and hmac.compare_digest(
        credentials.username.encode("utf-8"), expected_username.encode("utf-8")
    ) and hmac.compare_digest(credentials.password.encode("utf-8"), expected_password.encode("utf-8"))
    if not valid:
        raise HTTPException(
            status_code=401,
            detail="The API docs need a username and password (DOCS_USERNAME / DOCS_PASSWORD).",
            headers={"WWW-Authenticate": "Basic"},
        )
