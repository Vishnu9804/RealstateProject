"""FastAPI entrypoint.

Run with:
    uvicorn main:app --reload --reload-exclude "venv" --reload-exclude "*.db" --host 0.0.0.0 --port 8000

("venv", not "venv/*": Click 8.0+ auto-expands any CLI argument that looks
like a glob pattern AND actually matches files, against the current
directory, on Windows — before uvicorn ever sees it. "venv/*" matches
real entries under Backend/venv/, so Click silently explodes that one
argument into several, which uvicorn's own arg parser then rejects as
"unexpected extra arguments". A bare directory name never matches more
than itself, so it sidesteps the bug — and it's also the more correct
form: uvicorn's own reload-pattern resolver (Config.resolve_reload_patterns
in uvicorn/config.py) treats a bare existing directory as "exclude this
whole directory tree", which is exactly what's wanted here.)

--host 0.0.0.0 is not optional either: uvicorn defaults to binding only
127.0.0.1 (loopback), which accepts connections from this machine alone.
The WhatsApp inquiry form link (Config/settings.py's inquiry_form_base_url)
and the CORS allow-list (frontend_lan_origin below) are both built around
this machine's LAN IP specifically so a phone — or a browser tab on this
same machine opened via that LAN IP instead of "localhost" — can reach the
API. Without --host 0.0.0.0, every one of those LAN-IP requests gets
refused before FastAPI ever sees them, surfacing in the browser as "Could
not reach the backend."

Neither --reload-exclude is optional: uvicorn's --reload watches this
entire directory recursively by default, which includes venv/ and both
WhatsApp session stores (Service/WhatsAppDataFetchingService/session/
whatsapp_session.db and Service/WhatsAppInquiryHandlingService/session/
whatsapp_inquiry_session.db). Without excluding venv/, something as
ordinary as `pip install`-ing a new dependency mid-session (venv/ files
change) triggers a full server restart — killing BOTH live WhatsApp client
connections (and any pairing handshake in progress) along with them, even
though nothing about the application code itself changed. Without
excluding *.db, it's worse: those two session files are rewritten by the
neonize/whatsmeow client on ordinary WhatsApp traffic (e.g. every inbound
inquiry message), so the reload watcher restarts the whole process
practically every time someone messages the bot — which also wipes the
in-memory, per-process registration-form token store
(Service/WhatsAppInquiryHandlingService/form_token_service.py) and
invitation tracker, so a link sent moments earlier comes back "This link
is no longer valid" the instant it's opened, well before its real 24-hour
TTL. Both files live only under Service/*/session/, so this exclude
can't accidentally mask a real code change elsewhere.

On startup, spawns the WhatsApp client (Service/WhatsAppDataFetchingService/whatsapp_client.py) on a
background thread. It prints its own progress (pairing, group/personal-chat
selection, incoming messages) straight to this terminal — see
Middleware/step_logger.py.
"""

import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

if sys.platform == "win32":
    # The default Windows console codepage (cp1252) can't render the
    # Unicode block characters the QR code is drawn with, and silently
    # fails to print it. Force real UTF-8 output before anything else runs.
    import ctypes

    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from Controller.WhatsAppDataFetchingController.area_filter_controller import router as area_filter_router
from Controller.WhatsAppDataFetchingController.display_settings_controller import router as display_settings_router
from Controller.WhatsAppDataFetchingController.duplicate_detection_controller import router as duplicate_detection_router
from Controller.WhatsAppDataFetchingController.property_controller import router as property_router
from Controller.WhatsAppDataFetchingController.whatsapp_controller import router as whatsapp_router
from Controller.WhatsAppInquiryHandlingController.inquiry_form_controller import router as inquiry_form_router
from Controller.WhatsAppInquiryHandlingController.whatsapp_inquiry_controller import router as whatsapp_inquiry_router
from Controller.InstagramInquiryHandlingController.instagram_controller import router as instagram_router
from Config.settings import get_settings
from Database.client_session import init_client_db, is_client_database_configured
from Database.session import init_db, is_database_configured
from Middleware.logging_config import configure_logging
from Middleware import step_logger
from Service.WhatsAppDataFetchingService import area_filter_service, display_settings_service, duplicate_detection_service, whatsapp_service
from Service.WhatsAppInquiryHandlingService import whatsapp_inquiry_service
from Service.InstagramInquiryHandlingService import instagram_connection_service, instagram_polling_service

configure_logging()


async def _init_property_database() -> None:
    if is_database_configured():
        step_logger.step(
            "DATABASE_URL is set — initializing the database and loading saved settings... "
            "(if this database has been idle a while, e.g. Neon's free tier, waking it up can "
            "genuinely take a couple of minutes on this first connection — that's normal, not a freeze)"
        )
        # to_thread, not a direct call: these are blocking network calls, and
        # running them on the event loop thread would stall the OTHER
        # database's init below from making any progress until this one
        # finished — exactly the sequential wait this is meant to avoid.
        await asyncio.to_thread(init_db)
        await asyncio.to_thread(area_filter_service.load_from_database)
        await asyncio.to_thread(display_settings_service.load_from_database)
        await asyncio.to_thread(duplicate_detection_service.load_from_database)
        await asyncio.to_thread(instagram_connection_service.load_from_database)
        step_logger.success("Database ready — properties and settings will persist across restarts.")
    else:
        step_logger.info(
            "DATABASE_URL is not set — running with in-memory storage only. Nothing is lost while the "
            "server stays up, but properties and settings reset on restart until a database is connected."
        )


async def _init_client_database() -> None:
    if is_client_database_configured():
        step_logger.step(
            "CLIENT_DATABASE_URL is set — initializing the client database... "
            "(same possible multi-minute cold-start wait as the property database, if it's a "
            "separate idle Neon branch — running concurrently with it, not waiting for it first)"
        )
        await asyncio.to_thread(init_client_db)
        step_logger.success("Client database ready — inquiry-handling client records will persist across restarts.")
    else:
        step_logger.info(
            "CLIENT_DATABASE_URL is not set — whatsappInquiryHandling client records will use in-memory "
            "storage only until it's configured."
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Two independent Neon databases — each can have its own multi-minute
    # cold-start delay if idle. Running them concurrently means the total
    # wait is whichever one is slower, not both added together.
    await asyncio.gather(_init_property_database(), _init_client_database())

    step_logger.step("FastAPI server is up. Launching WhatsApp client in the background...")
    whatsapp_service.start_agent_in_background()
    # Deliberately staggered, not started together: the underlying neonize
    # (whatsmeow) Go library has an internal shared map that isn't
    # synchronized against concurrent client construction — starting both
    # WhatsApp clients (this app runs two: whatsappDataFetching and
    # whatsappInquiryHandling) at the exact same instant reliably crashes
    # the ENTIRE process with a Go-runtime "fatal error: concurrent map
    # writes", which is not a Python exception and cannot be caught or
    # recovered from. Giving the first client a head start to finish its
    # own Go-side setup avoids that specific collision window. This is a
    # mitigation, not a guarantee the underlying library is safe under all
    # concurrent use — if this crash recurs later (not just at startup),
    # the real fix is running the two clients in separate OS processes.
    await asyncio.sleep(8)
    whatsapp_inquiry_service.start_agent_in_background()
    # No stagger needed here, unlike the two WhatsApp clients above — the
    # Instagram connection is a plain HTTPS keepalive check, not a
    # neonize/whatsmeow Go client, so it shares none of that library's
    # concurrent-construction crash risk.
    instagram_connection_service.start_background_keepalive()
    # Also inert until Instagram is connected (see its own module
    # docstring) — safe to start unconditionally alongside the keepalive
    # loop, no stagger needed for the same reason as above.
    instagram_polling_service.start_background_polling()
    yield


app = FastAPI(title="Real Estate WhatsApp Ingestion API", lifespan=lifespan)

# The frontend (Frontend/, Vite dev server) runs on a different origin than
# this API, so without CORS the browser blocks every request from it — a
# failure curl/pytest would never catch, only a real browser would. An
# explicit allow-list, not "*": this is dev-only for now, and the production
# frontend origin gets added here once it's hosted (Step 12).
#
# `frontend_lan_origin` (FRONTEND_LAN_ORIGIN in .env) adds one more allowed
# origin on top of the two localhost ones — set it to "http://<this
# machine's LAN IP>:5173" so a phone on the same network can actually load
# the registration/update form (see Config/settings.py's
# inquiry_form_base_url) and have its browser's API calls accepted here.
_cors_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
if get_settings().frontend_lan_origin:
    _cors_origins.append(get_settings().frontend_lan_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(whatsapp_router, prefix="/api")
app.include_router(area_filter_router, prefix="/api")
app.include_router(display_settings_router, prefix="/api")
app.include_router(duplicate_detection_router, prefix="/api")
app.include_router(property_router, prefix="/api")
app.include_router(whatsapp_inquiry_router, prefix="/api")
app.include_router(inquiry_form_router, prefix="/api")
app.include_router(instagram_router, prefix="/api")


@app.get("/")
def root() -> dict:
    return {"service": "real-estate-whatsapp-ingestion", "status": "running"}
