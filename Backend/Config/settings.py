"""Central place every other module reads secrets/environment config from —
nothing outside this file should call `os.getenv(...)` directly. Values come
from `Backend/.env` (see `.env.example` for the variables it must define).

Every later stage of the pipeline depends on a value defined here:
Z.ai GLM structuring (Agent/) needs `zai_api_key`, the Postgres+pgvector
data layer needs `database_url`. Both are intentionally blank until the
final "connect the database" step — everything up to that point is built and
runnable against these placeholders.
"""

import socket
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _detect_lan_ip() -> str:
    """Best-effort LAN IP for this machine — the address other devices on
    the same network (e.g. a phone) would use to reach it, unlike
    "localhost"/"127.0.0.1" which only ever resolves to the device it's
    opened on. Connecting a UDP socket never actually sends a packet (UDP
    is connectionless); it only asks the OS to pick the local interface/IP
    it would route through for that destination, which is what we want.
    Falls back to loopback if there's no network route at all (e.g. no
    interface up), matching the old hardcoded behavior for that edge case."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    zai_api_key: str = ""
    database_url: str = ""
    # Separate Neon Postgres database for whatsappInquiryHandling's client
    # records (Database/client_session.py) — deliberately independent from
    # `database_url` above (the property-listing database used by
    # whatsappDataFetching), so the two features' data can never collide.
    client_database_url: str = ""
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
    # How long the per-user debounce buffer (Service/WhatsAppInquiryHandlingService/
    # inquiry_buffer_service.py) waits after a user's LAST message before
    # flushing their buffered batch to the LLM, in SECONDS. Every new message
    # from that same number restarts this countdown, so it only fires once
    # they've actually stopped typing.
    inquiry_buffer_window_seconds: int = Field(default=10, gt=0)
    # Base URL the registration/update form link (sent over WhatsApp — see
    # Service/WhatsAppInquiryHandlingService/inquiry_pipeline_service.py)
    # is built from: "{inquiry_form_base_url}/{token}". Left blank by
    # default: a link containing "localhost" is unreachable from a real
    # phone (it resolves to the phone's own loopback, not this machine), so
    # rather than hardcode that broken default, `_fill_lan_defaults` below
    # fills this in with this machine's actual LAN IP (e.g.
    # "http://192.168.1.50:5173/whatsapp-inquiry") whenever it's left unset
    # — set it explicitly here only to point at a real deployed frontend
    # (e.g. once Step 12 hosts one) instead of a LAN dev server.
    inquiry_form_base_url: str = ""
    # Extra origin main.py's CORS allow-list accepts, beyond the hardcoded
    # localhost ones. Left blank by default and auto-filled (see
    # `_fill_lan_defaults`) to match the auto-detected LAN IP above, so a
    # phone's browser loading the form page is allowed to call this API
    # without any manual per-machine .env edits. Set explicitly only to
    # override that detection (e.g. a real deployed frontend origin).
    frontend_lan_origin: str = ""

    @model_validator(mode="after")
    def _fill_lan_defaults(self) -> "Settings":
        """Auto-detects this machine's LAN IP once, at startup, and uses it
        to fill in whichever of the two fields above weren't explicitly set
        via .env — see their docstrings. Never overrides an explicit
        setting, so a real production URL configured here always wins."""
        if not self.inquiry_form_base_url or not self.frontend_lan_origin:
            lan_ip = _detect_lan_ip()
            if not self.inquiry_form_base_url:
                self.inquiry_form_base_url = f"http://{lan_ip}:5173/whatsapp-inquiry"
            if not self.frontend_lan_origin and lan_ip != "127.0.0.1":
                self.frontend_lan_origin = f"http://{lan_ip}:5173"
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached so `.env` is only read once per process; call this instead of
    constructing `Settings()` directly."""
    return Settings()
