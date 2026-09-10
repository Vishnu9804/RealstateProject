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
    # Google Gemini API key used by the inquiry-classification stage
    # (Agent/WhatsAppInquiryHandlingAgent/inquiry_classifier.py) — separate
    # from zai_api_key above, which is only used by the property-structuring
    # stage (Agent/WhatsAppDataFetchingAgent/property_structurer.py).
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash-lite"
    # Single Postgres+pgvector database for the whole app — both the
    # property-listing data (whatsappDataFetching) and the client records
    # (whatsappInquiryHandling, Database/client_session.py) live in this one
    # database, under separate tables. There used to be a second
    # `client_database_url` variable here, but it was always set to the
    # same connection string in practice; keeping two names for one value
    # only invited them to drift apart, and having two code paths open two
    # connections to the same database at startup was the direct cause of a
    # race condition creating the pgvector extension. See Database/session.py's
    # init_db() — the single place all tables (both features') are created.
    database_url: str = ""
    # Has a working default so `.env` only needs the two secrets above —
    # override with a ZAI_MODEL env var if a different model is wanted.
    # Moved off Gemini to Z.ai for cost, then off GLM-4.7-FlashX to GLM-4.6
    # for accuracy. FlashX was chosen as "meaningfully cheaper per token at
    # production volume", but measured head-to-head on real broker messages
    # it could not do this job reliably:
    #
    #   FlashX  4/6 correct, 30-241s, fell into repetition loops, and
    #           repeatedly counted 8 properties then returned 5 or 7
    #   GLM-4.6 11/11 correct, 41-47s, no loops, no undercounts
    #
    # The volume argument also does not hold here: batching (up to 10
    # messages per request) turns ~500 messages/day into roughly 50 calls,
    # so the per-token premium is small in absolute terms — while a dropped
    # property is a lost client opportunity that never reaches the
    # dashboard. Set ZAI_MODEL=glm-4.7-flashx to go back.
    zai_model: str = "glm-4.6"
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
    # "http://192.168.1.50:5174/enquire") whenever it's left unset — set it
    # explicitly here only to point at a real deployed site instead of a LAN
    # dev server.
    #
    # It points at the PUBLIC SITE (LandingPage/, port 5174), not the
    # internal tool: the form is no longer a standalone page of its own but
    # the requirements section at the bottom of the landing page, and
    # "/enquire/{token}" is the route that opens that page already scrolled
    # to it and prefilled (see LandingPage/src/App.tsx).
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
                self.inquiry_form_base_url = f"http://{lan_ip}:5174/enquire"
            if not self.frontend_lan_origin and lan_ip != "127.0.0.1":
                self.frontend_lan_origin = f"http://{lan_ip}:5173"
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached so `.env` is only read once per process; call this instead of
    constructing `Settings()` directly."""
    return Settings()
