"""Central place every other module reads secrets/environment config from —
nothing outside this file should call `os.getenv(...)` directly. Values come
from `Backend/.env` (see `.env.example` for the variables it must define).

Every later stage of the pipeline depends on a value defined here:
Gemini structuring (Agent/) needs `gemini_api_key`, the Postgres+pgvector
data layer needs `database_url`. Both are intentionally blank until the
final "connect the database" step — everything up to that point is built and
runnable against these placeholders.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: str = ""
    database_url: str = ""


@lru_cache
def get_settings() -> Settings:
    """Cached so `.env` is only read once per process; call this instead of
    constructing `Settings()` directly."""
    return Settings()
