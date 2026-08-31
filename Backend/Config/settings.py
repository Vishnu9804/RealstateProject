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

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: str = ""
    database_url: str = ""
    # Separate Neon Postgres database for whatsappInquiryHandling's client
    # records (Database/client_session.py) — deliberately independent from
    # `database_url` above (the property-listing database used by
    # whatsappDataFetching), so the two features' data can never collide.
    client_database_url: str = ""
    # Has a working default so `.env` only needs the two secrets above —
    # override with a GEMINI_MODEL env var if a different model is wanted.
    # gemini-2.5-flash (the original default here) was retired for new
    # users; confirmed against the real API during Step 12 that
    # gemini-3.6-flash is the current replacement.
    gemini_model: str = "gemini-3.6-flash"
    # How long the buffering stage (Service/WhatsAppDataFetchingService/message_buffer_service.py)
    # waits for a batch to reach 10 messages before flushing whatever it
    # has anyway, in MINUTES (e.g. 60 = 1 hour, 3 = 3 minutes — useful for
    # fast local testing without waiting a full hour).
    batch_window_minutes: int = Field(default=60, gt=0)
    # How long the per-user debounce buffer (Service/WhatsAppInquiryHandlingService/
    # inquiry_buffer_service.py) waits after a user's LAST message before
    # flushing their buffered batch to the LLM, in SECONDS. Every new message
    # from that same number restarts this countdown, so it only fires once
    # they've actually stopped typing.
    inquiry_buffer_window_seconds: int = Field(default=10, gt=0)
    # Base URL the registration/update form link (sent over WhatsApp — see
    # Service/WhatsAppInquiryHandlingService/inquiry_pipeline_service.py)
    # is built from: "{inquiry_form_base_url}/{token}". Points at the Vite
    # dev server by default, matching main.py's CORS allow-list — the form
    # PAGE itself doesn't exist yet (a later step), so links sent today
    # won't resolve to anything real until then.
    inquiry_form_base_url: str = "http://localhost:5173/whatsapp-inquiry"


@lru_cache
def get_settings() -> Settings:
    """Cached so `.env` is only read once per process; call this instead of
    constructing `Settings()` directly."""
    return Settings()
