"""Central place every other module reads secrets/environment config from —
nothing outside this file should call `os.getenv(...)` directly. Values come
from `Backend/.env` (see `.env.example` for the variables it must define).

Every later stage of the pipeline depends on a value defined here:
Z.ai GLM structuring (Agent/) needs `zai_api_key`, the Postgres+pgvector
data layer needs `database_url`. Both are intentionally blank until the
final "connect the database" step — everything up to that point is built and
runnable against these placeholders.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    zai_api_key: str = ""
    database_url: str = ""
    # Has a working default so `.env` only needs the two secrets above —
    # override with a ZAI_MODEL env var if a different model is wanted.
    # Switched from Gemini to GLM-4.7-FlashX (Z.ai) — meaningfully cheaper
    # per token at production volume, which is what the earlier Gemini
    # model was costing.
    zai_model: str = "glm-4.7-flashx"
    # OpenAI-compatible chat-completions endpoint. Override with ZAI_BASE_URL
    # only if Z.ai's regional/mainland endpoint is needed instead.
    zai_base_url: str = "https://api.z.ai/api/paas/v4/"
    # How long the buffering stage (Service/WhatsAppDataFetchingService/message_buffer_service.py)
    # waits for a batch to reach 10 messages before flushing whatever it
    # has anyway, in MINUTES (e.g. 60 = 1 hour, 3 = 3 minutes — useful for
    # fast local testing without waiting a full hour).
    batch_window_minutes: int = Field(default=60, gt=0)


@lru_cache
def get_settings() -> Settings:
    """Cached so `.env` is only read once per process; call this instead of
    constructing `Settings()` directly."""
    return Settings()
