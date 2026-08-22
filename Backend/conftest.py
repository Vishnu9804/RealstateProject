"""Marks Backend/ as pytest's rootdir, so `from Model.x import Y` /
`from Service.x import Y` style imports (used throughout this codebase)
resolve the same way under pytest as they do when running the app
normally from this directory.

Also forces the test suite to stay hermetic: Backend/.env holds real
production credentials once the app is actually deployed/connected (see
Config/settings.py), and the whole point of Service/property_vector_store.py
and friends' "falls back to in-memory when no DB is configured" design is
that the duplicate-detection tests exercise that in-memory path on purpose.
Without this, `pytest` would silently start reading and writing a real
database over the network on every run — slow, and a real risk of leaving
test rows in production data. DATABASE_URL is cleared before any test
module (or its imports) can read settings, so is_database_configured()
reads False everywhere for the whole test session regardless of what's in
.env.
"""

import os

os.environ["DATABASE_URL"] = ""

from Config.settings import get_settings  # noqa: E402

get_settings.cache_clear()
