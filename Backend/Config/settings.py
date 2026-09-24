"""Central place every other module reads secrets/environment config from —
nothing outside this file should call `os.getenv(...)` directly. Values come
from `Backend/.env` (see `.env.example` for the variables it must define).

Every later stage of the pipeline depends on a value defined here:
Z.ai GLM structuring (Agent/) needs `zai_api_key_property` (and inquiry
classification `zai_api_key_inquiry`), the Postgres+pgvector
data layer needs `database_url`. Both are intentionally blank until the
final "connect the database" step — everything up to that point is built and
runnable against these placeholders.
"""

import socket
from functools import lru_cache

from pydantic import AliasChoices, Field, model_validator
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

    # Two Z.ai accounts, one key each, so the two workloads can never queue
    # behind each other (Z.ai's concurrency limit is per ACCOUNT, and a bulk
    # property import used to hold the only slot for minutes while a client
    # inquiry waited):
    #   ZAI_API_KEY_PROPERTY — property + broker-requirement structuring
    #                          (the bulk imports). This was ZAI_API_KEY; the
    #                          old name is still read if the new one is unset,
    #                          so a host that hasn't renamed the variable yet
    #                          keeps working.
    #   ZAI_API_KEY_INQUIRY  — client-inquiry classification only. Leave blank
    #                          to fall back to the property key (inquiries
    #                          then share that account, exactly as before).
    zai_api_key_property: str = Field(default="", validation_alias=AliasChoices("ZAI_API_KEY_PROPERTY", "ZAI_API_KEY"))
    zai_api_key_inquiry: str = Field(default="", validation_alias=AliasChoices("ZAI_API_KEY_INQUIRY"))
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
    # Model for the inquiry-classification stage (Agent/WhatsAppInquiryHandlingAgent/
    # inquiry_classifier.py) — deciding whether an incoming WhatsApp message is
    # property-related at all. A much simpler fixed-shape judgment (one bool +
    # a short reason) than property/requirement extraction, so GLM-4.7-FlashX's
    # speed and lower cost apply here without the accuracy problems that moved
    # zai_model off it above. Runs on zai_api_key_inquiry (falls back to zai_api_key_property if blank).
    zai_inquiry_model: str = "glm-4.7-flashx"
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
    # Not used by any message right now — the Instagram DM sequence used to
    # tell people to call this number for a site visit, and no longer does
    # (it is now the property details plus the requirements-form link). Kept
    # so an existing BUSINESS_CONTACT_PHONE line in .env still loads.
    business_contact_phone: str = ""

    # --- Instagram (Meta's OFFICIAL Instagram Platform API) --------------
    #
    # Everything below belongs to ONE Meta app, configured once in the Meta
    # App Dashboard under the "Instagram" product -> "API setup with
    # Instagram business login". This replaced the previous unofficial
    # username/password login: Instagram flags automated access on a normal
    # account ("we detected automated behaviour"), whereas the official API
    # is the sanctioned path and pushes events to us instead of us asking
    # "anything new?" on a timer.
    #
    # instagram_app_id / instagram_app_secret are the *Instagram* app ID and
    # secret shown on that same API-setup screen (NOT the Facebook app ID at
    # the top of the dashboard). The secret does two separate jobs: it signs
    # nothing of ours, but Meta signs every webhook delivery with it
    # (X-Hub-Signature-256), and it is required to swap an OAuth code for a
    # token.
    instagram_app_id: str = ""
    instagram_app_secret: str = ""
    # Optional second secret checked only if the signature does not match
    # the one above. Meta has two secrets on a combined app (the Facebook
    # "App secret" under App settings -> Basic, and the Instagram one), and
    # which of them signs a delivery has changed between app types. Setting
    # this costs nothing and removes an entire class of "every webhook is
    # rejected as unsigned" confusion.
    facebook_app_secret: str = ""
    # The string typed into the dashboard's "Verify token" box. Meta echoes
    # it back once, on the GET handshake that activates the callback URL —
    # it is not a credential Meta issues, it is a shared secret WE invent so
    # that nobody else can point Meta's webhook config at this server. Any
    # long random string works, as long as the same value is in both places.
    instagram_webhook_verify_token: str = ""
    # Where Instagram sends the browser back after the business-login
    # consent screen. Must match one of the "OAuth redirect URIs" configured
    # in the dashboard EXACTLY (scheme, host, path, no trailing slash
    # difference). Leave blank to use the token-paste connection path
    # instead, which needs no redirect URI at all.
    instagram_redirect_uri: str = ""
    # The PUBLIC address Meta reaches this server on, e.g.
    # "https://your-tunnel.ngrok-free.dev" (no trailing slash). Used only to
    # print the exact callback URL and OAuth redirect URI on the Connection
    # page. Leave blank to derive them from whatever address the browser
    # reached this API on — which is right once the app is hosted, but NOT
    # while developing, where the browser reaches the backend on localhost
    # while Meta reaches it through a tunnel. Set it to the tunnel URL in
    # that case and the page prints what actually needs pasting.
    instagram_public_base_url: str = ""
    # Pinned rather than "latest": a Graph version is a contract, and a
    # silent bump can change payload shapes underneath a running deployment.
    instagram_graph_version: str = "v23.0"
    # Ceiling on one Instagram HTTP call. Every call this app makes is
    # small; a hung socket must not hold a webhook worker thread forever.
    instagram_api_timeout_seconds: int = Field(default=20, gt=0)

    # Origin the internal tool (Frontend/) is served from, used ONLY to send
    # the browser back to the Connection page after the Instagram OAuth
    # round-trip. Left blank and auto-filled from the detected LAN IP (see
    # _fill_lan_defaults) exactly like frontend_lan_origin.
    frontend_base_url: str = ""

    # --- AuthManagement (login accounts, Service/AuthManagementService/) ---
    #
    # Signs/verifies every login JWT (Service/AuthManagementService/
    # token_service.py). Left blank by default like the other secrets above
    # — a blank secret would let anyone forge a token, so token_service
    # refuses to start signing until this is actually set in `.env`. There is
    # deliberately no auto-generated fallback (unlike e.g.
    # inquiry_form_base_url's LAN-IP autofill): a secret that's regenerated
    # on every restart would invalidate every existing login each time the
    # server restarts, and a secret that's auto-generated once and silently
    # persisted somewhere is worse than just asking for one explicitly.
    jwt_secret_key: str = ""
    # Lifetime of ONE token, not of a session: the Frontend swaps its token
    # for a fresh one (POST /api/auth/refresh) while the user is active, so an
    # active user is never signed out, and signs an idle user out itself after
    # 12 hours without activity (Frontend/src/state/AuthProvider.tsx). A day
    # gives that idle limit comfortable headroom, and bounds how long a token
    # nobody is refreshing any more can still be used.
    jwt_expiry_hours: int = Field(default=24, gt=0)
    # First admin account, created once at startup if the `users` table (or
    # its in-memory fallback) has no admin row yet — see
    # Service/AuthManagementService/user_store.py's ensure_admin_seeded. Left
    # blank by default; if either is unset on a database with no admin yet,
    # startup logs a warning instead of guessing a default username/password
    # (a guessed default sitting in the database unnoticed is a worse outcome
    # than a clear "you have no admin account yet" warning). Never touches an
    # admin row that already exists, so changing these after the first admin
    # is created has no effect — that account's password is then only ever
    # changed via PATCH /api/auth/me/password while logged in as that admin.
    admin_username: str = ""
    admin_password: str = ""

    # --- per-identity daily allowances (see Middleware/daily_quota.py) ---
    #
    # How much ONE personal WhatsApp number, or ONE Instagram account, may
    # cost this backend in a single day (the day turning over at 6 AM IST).
    # They are settings rather than constants for one reason: the right
    # number is a business judgement about this business's customers, not a
    # property of the code, and finding it must never require a code change.
    #
    # Set generously on purpose. Every one of these is several times what a
    # genuine enquiry has ever needed, because the cost of being wrong in
    # the two directions is not remotely symmetric: too high merely lets a
    # flood run a little longer before it is stopped, while too low turns a
    # real customer away mid-conversation. Set any of them to 0 to turn
    # that particular limit off entirely.
    whatsapp_daily_message_limit: int = Field(default=20, ge=0)
    whatsapp_daily_word_limit: int = Field(default=200, ge=0)
    instagram_daily_comment_limit: int = Field(default=10, ge=0)
    instagram_daily_dm_limit: int = Field(default=10, ge=0)

    # --- embedding model memory (Service/WhatsAppDataFetchingService/
    # embedding_service.py) ---
    #
    # How long the sentence-transformers model may sit unused before it is
    # released from memory, in MINUTES. It reloads automatically on the next
    # embedding (a second or two, from the on-disk model cache), so nothing
    # it PRODUCES changes — same model, same weights, same vectors. The only
    # thing this changes is how much RAM this process holds while nobody is
    # saving anything.
    #
    # It is a setting rather than a constant because the right number is a
    # property of how the hosting bills RAM and of how bursty the working
    # day is, neither of which is a fact about the code. Embedding happens
    # only when a property, builder project, client requirement or broker
    # requirement is saved: clustered during the day, silent overnight.
    #
    # Set to 0 to switch releasing off entirely — the model then stays
    # loaded from its first use until the process exits, exactly as it
    # behaved before this setting existed. Same "0 turns it off" convention
    # as the daily allowances above.
    embedding_model_idle_unload_minutes: int = Field(default=15, ge=0)

    # Hugging Face access token (HF_TOKEN in .env) used when the embedding
    # model is downloaded. Optional: the model is public, so blank still works
    # — a token only lifts the anonymous download rate limit and silences the
    # "unauthenticated requests" warning. Settings reads `.env` itself without
    # exporting it to the process environment, which is where huggingface_hub
    # looks, so embedding_service hands the value over (see _apply_hf_token).
    # On Railway, HF_TOKEN is a real environment variable and works either way.
    hf_token: str = ""

    # Whether the Settings page may change the selected areas
    # (ALLOW_AREA_CHANGE in .env). False by default: the area list decides
    # which captured properties are Main vs Outsider, so editing it is locked
    # unless explicitly switched on. Enforced by the API itself
    # (Controller/WhatsAppDataFetchingController/area_filter_controller.py),
    # not only hidden in the UI. Read once at startup — restart after changing.
    allow_area_change: bool = False

    # Whether a property's photos are sent to a client as ONE WhatsApp album
    # (PROPERTY_PHOTOS_AS_ALBUM in .env), with the property details as its
    # caption. False by default: the album relies on WhatsApp accepting a
    # linked-device album, which can only be confirmed against a live
    # WhatsApp account, and a shortlist that does not show up is far worse
    # than one where each photo arrives as its own message (what false does).
    # Turn it on, send yourself a shortlist, and leave it on only if the
    # photos and details arrive. Read once at startup — restart after changing.
    property_photos_as_album: bool = False

    # Shared access key for the Dashboard's usage endpoints (LLM Cost, Neon
    # DB, Backend, Message to Model) — see Middleware/dashboard_access.py.
    # Blank (the default) leaves them open, exactly as before. Set it before
    # hosting: the Message to Model feed carries raw WhatsApp message text.
    # The Dashboard must be built with the same value as VITE_DASHBOARD_KEY.
    dashboard_key: str = ""

    # HTTP Basic Auth credentials required to open /docs, /redoc and
    # /openapi.json (see Middleware/docs_access.py) — without these, once
    # this backend is hosted somewhere public, anyone with the URL can browse
    # the entire API surface (every route, every request/response shape)
    # with no login at all. Blank (the default) leaves them open, same
    # convention as dashboard_key above — fine on your own machine. Set BOTH
    # before hosting.
    docs_username: str = ""
    docs_password: str = ""

    # Comma-separated list of extra frontend origins the CORS allow-list
    # accepts, on top of the hardcoded localhost ones and the auto-detected
    # LAN origin above — this is where the real Cloudflare Pages domain(s)
    # go once the frontend(s) are hosted, e.g.
    # "https://real-estate-ops.pages.dev,https://real-estate-site.pages.dev".
    # Leave blank in local development.
    extra_cors_origins: str = ""

    # Folder every runtime-written file lives under (see Config/paths.py):
    # pending batches, the area knowledge base, the usage stats and — only
    # when this is set — the WhatsApp session files. Blank (the default)
    # keeps today's local layout: the project root, one level above
    # Backend/. On Railway it must be the mount path of a Volume (/data), so
    # all of it survives restarts and redeploys. RAILWAY_VOLUME_MOUNT_PATH,
    # which Railway sets by itself whenever a Volume is attached, is used
    # when DATA_DIR is not set, so a forgotten variable still lands on the
    # Volume. Read once at startup.
    data_dir: str = Field(default="", validation_alias=AliasChoices("DATA_DIR", "RAILWAY_VOLUME_MOUNT_PATH"))

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
        # The Instagram OAuth callback has to redirect a real browser back
        # to the internal tool, so it needs an absolute origin. Prefers an
        # explicit setting, then the LAN origin already resolved above, and
        # finally plain localhost — which is correct for the common case of
        # the operator running the frontend on this same machine.
        if not self.frontend_base_url:
            self.frontend_base_url = self.frontend_lan_origin or "http://localhost:5173"
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached so `.env` is only read once per process; call this instead of
    constructing `Settings()` directly."""
    return Settings()
