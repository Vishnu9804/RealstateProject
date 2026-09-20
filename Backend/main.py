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
entire directory recursively by default, which includes venv/ and every
linked WhatsApp number's session store (one .db file per connection under
Service/WhatsAppDataFetchingService/session/ — see
whatsapp_connection_manager.py; there can be several now that the
Connection page supports linking more than one number). Without excluding
venv/, something as ordinary as `pip install`-ing a new dependency
mid-session (venv/ files change) triggers a full server restart — killing
every live WhatsApp connection (and any pairing handshake in progress)
along with them, even though nothing about the application code itself
changed. Without excluding *.db, it's worse: those session files are
rewritten by the neonize/whatsmeow client on ordinary WhatsApp traffic (e.g.
every inbound inquiry message), so the reload watcher restarts the whole
process practically every time someone messages the bot — which also wipes
the in-memory, per-process registration-form token store
(Service/WhatsAppInquiryHandlingService/form_token_service.py) and
invitation tracker, so a link sent moments earlier comes back "This link
is no longer valid" the instant it's opened, well before its real 24-hour
TTL. Every session file lives only under Service/*/session/, so this
exclude can't accidentally mask a real code change elsewhere.

On startup, spawns the WhatsApp client (Service/WhatsAppDataFetchingService/whatsapp_client.py) on a
background thread. It prints its own progress (pairing, group/personal-chat
selection, incoming messages) straight to this terminal — see
Middleware/step_logger.py.
"""

import asyncio
import os
import sys
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

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

from Controller.AgentManagementController.agent_controller import router as agent_router
from Controller.AuthManagementController.auth_controller import router as auth_router
from Controller.AuthManagementController.user_controller import router as user_management_router
from Controller.BrokerRequirementController.broker_requirement_controller import router as broker_requirement_router
from Controller.BrokerRequirementController.requirement_matching_controller import router as requirement_matching_router
from Controller.BuilderProjectController.builder_project_controller import router as builder_project_router
from Controller.ClientPropertyMatchingController.matching_controller import router as matching_router
from Controller.WhatsAppDataFetchingController.area_filter_controller import router as area_filter_router
from Controller.WhatsAppDataFetchingController.area_knowledge_controller import router as area_knowledge_router
from Controller.WhatsAppDataFetchingController.display_settings_controller import router as display_settings_router
from Controller.WhatsAppDataFetchingController.property_controller import router as property_router
from Controller.WhatsAppDataFetchingController.soldout_property_controller import router as soldout_property_router
from Controller.WhatsAppDataFetchingController.whatsapp_connections_controller import router as whatsapp_connections_router
from Controller.WhatsAppDataFetchingController.whatsapp_controller import router as whatsapp_router
from Controller.WhatsAppInquiryHandlingController.inquiry_form_controller import router as inquiry_form_router
from Controller.WhatsAppInquiryHandlingController.phone_verification_controller import router as phone_verification_router
from Controller.WhatsAppInquiryHandlingController.whatsapp_inquiry_controller import router as whatsapp_inquiry_router
from Controller.InstagramInquiryHandlingController.instagram_controller import router as instagram_router
from Controller.InstagramInquiryHandlingController.instagram_webhook_controller import router as instagram_webhook_router
from Controller.LLMUsageController.llm_usage_controller import router as llm_usage_router
from Controller.NeonUsageController.neon_usage_controller import router as neon_usage_router
from Controller.BackendUsageController.backend_usage_controller import router as backend_usage_router
from Controller.LandingPageController.landing_page_controller import router as landing_page_router
from Controller.PropertySharingController.property_share_controller import router as property_share_router
from Config.settings import get_settings
from Database.session import init_db, is_database_configured
from Middleware.cpu_meter import CpuMeterMiddleware, install_threadpool_meter
from Middleware.dashboard_access import require_dashboard_key
from Middleware.logging_config import configure_logging
from Middleware.public_rate_limit import PublicRateLimitMiddleware
from Middleware import step_logger
from Service.AgentManagementService import handoff_template_service, visit_reminder_service
from Service.AuthManagementService import user_store
from Service.AuthManagementService.auth_dependencies import get_current_user
from Service.PropertySharingService import property_share_template_service
from Service.ClientPropertyMatchingService import scheduled_recompute_service
from Service.WhatsAppDataFetchingService import area_filter_service, area_knowledge_service, display_settings_service, pending_batch_store, whatsapp_service
from Service.WhatsAppInquiryHandlingService import inquiry_connection_store, whatsapp_inquiry_service
from Service.InstagramInquiryHandlingService import instagram_connection_service
from Service.BackendUsageService import cpu_usage_service
from Service.LLMUsageService import llm_usage_service, message_model_service
from Service.NeonUsageService import neon_usage_service

configure_logging()


async def _init_database() -> None:
    """Single init path for the one shared database — see
    Database/session.py's init_db() docstring for why property tables and
    client-records tables are created from that one function rather than
    two independent ones running concurrently."""
    if is_database_configured():
        step_logger.step(
            "DATABASE_URL is set — initializing the database and loading saved settings... "
            "(if this database has been idle a while, e.g. Neon's free tier, waking it up can "
            "genuinely take a couple of minutes on this first connection — that's normal, not a freeze)"
        )
        # to_thread, not a direct call: these are blocking network calls, and
        # running them on the event loop thread would stall the heartbeat
        # task below from ever getting a turn to print its reassurance line.
        await asyncio.to_thread(init_db)
        await asyncio.to_thread(area_filter_service.load_from_database)
        await asyncio.to_thread(display_settings_service.load_from_database)
        await asyncio.to_thread(instagram_connection_service.load_from_database)
        await asyncio.to_thread(handoff_template_service.load_from_database)
        await asyncio.to_thread(property_share_template_service.load_from_database)
        # Which of our linked numbers each client's inquiry arrived on, so
        # the first outbound message after a restart still goes out from the
        # same number they originally messaged rather than the default one.
        await asyncio.to_thread(inquiry_connection_store.load_from_database)
        step_logger.success(
            "Database ready — properties, client records, and settings will persist across restarts."
        )
    else:
        step_logger.info(
            "DATABASE_URL is not set — running with in-memory storage only. Nothing is lost while the "
            "server stays up, but properties, client records, and settings reset on restart until a "
            "database is connected."
        )


async def _startup_heartbeat() -> None:
    """The database init above can go up to ~60-120s with zero output on a
    cold Neon compute (see the STEP 1 log line) — from the terminal that
    looks identical to a frozen process. This purely prints a reassurance
    line every 15s while that wait is in progress; it does not touch the
    init logic itself and is cancelled the instant the database is ready."""
    elapsed = 0
    while True:
        await asyncio.sleep(15)
        elapsed += 15
        step_logger.info(
            f"Still waiting on the database connection... ({elapsed}s elapsed — "
            "this is expected on a cold Neon start, the process is not frozen)"
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # FIRST, before anything touches the database: the Neon usage history is
    # a chronological list of wake-ups, and init_db()'s own queries are
    # themselves the first wake-up of this run. Loading afterwards would
    # append the older history behind the newer entries. Non-fatal, like the
    # other file-backed stats below.
    try:
        await asyncio.to_thread(neon_usage_service.load_from_disk)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Could not load the Neon usage history (the app is unaffected): {exc!r}")

    # The Backend tab's vCPU history and the Message to Model log — plain
    # files at the project root like the Neon history above, loaded before
    # anything can record into them, and never fatal.
    try:
        await asyncio.to_thread(cpu_usage_service.load_from_disk)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Could not load the vCPU usage history (the app is unaffected): {exc!r}")
    try:
        await asyncio.to_thread(message_model_service.load_from_disk)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Could not load the message-to-model log (the app is unaffected): {exc!r}")

    heartbeat = asyncio.create_task(_startup_heartbeat())
    try:
        await _init_database()
    finally:
        heartbeat.cancel()

    # One query: login accounts are served from memory afterwards (no per-request DB cost).
    try:
        await asyncio.to_thread(user_store.load_and_seed)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Could not load login accounts — nobody can sign in until this is fixed: {exc!r}")

    # Loaded AFTER the database step on purpose: it canonicalises the area
    # spellings in its file against the client's selected areas, which the
    # step above is what restores. Has nothing to do with DATABASE_URL
    # otherwise — the knowledge base is a plain file at the project root
    # (see area_knowledge_service's docstring for why it lives outside
    # Backend/) and works identically with no database configured. Wrapped
    # because it is a by-product of the pipeline, never a prerequisite for
    # it: a knowledge base that fails to load must not stop the server.
    try:
        await asyncio.to_thread(area_knowledge_service.load_from_disk)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Could not load the area knowledge base (the pipeline is unaffected): {exc!r}")

    # Same reasoning as the area knowledge base just above: a plain file at
    # the project root (see llm_usage_service's own docstring), loaded as a
    # by-product, never a prerequisite — a usage file that fails to load
    # must not stop the server.
    try:
        await asyncio.to_thread(llm_usage_service.load_from_disk)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Could not load the LLM usage stats (the pipeline is unaffected): {exc!r}")

    # Everything this process has used so far is start-up — recorded once for
    # the Backend tab, before any request or background job can run.
    cpu_usage_service.record_startup()

    # Started BEFORE the connections below, so anything a previous run could
    # not structure (a Z.ai rate-limit window, an outage, a crash mid-batch)
    # is already being retried by the time new messages start arriving. With
    # nothing held it is one directory listing every 20s and no other work —
    # see pending_batch_store's docstring for why captured messages are never
    # dropped on a failed batch any more.
    pending_batch_store.start_retry_worker_in_background()

    step_logger.step("FastAPI server is up. Launching WhatsApp connections in the background...")
    # whatsapp_service owns wiring the property-message handler AND starting
    # whatsapp_connection_manager, which owns every linked WhatsApp number
    # (however many there are) and staggers their client construction
    # internally — see its own module docstring's Concurrency note for why
    # that matters: the underlying neonize (whatsmeow) Go library has an
    # internal shared map that isn't synchronized against concurrent client
    # construction, and starting two at the exact same instant can crash the
    # ENTIRE process with an unrecoverable Go-runtime error.
    whatsapp_service.start_agent_in_background()
    whatsapp_inquiry_service.start_agent_in_background()
    # No stagger needed here, unlike the WhatsApp connections above — the
    # Instagram connection is a plain HTTPS call against Meta's Graph API,
    # not a neonize/whatsmeow Go client, so it shares none of that library's
    # concurrent-construction crash risk.
    #
    # This is the ONLY recurring Instagram work left. The old comment/DM
    # poller that ran every 8 seconds is gone: Instagram now PUSHES events to
    # POST /api/instagram/webhook (see instagram_webhook_router below and
    # Service/InstagramInquiryHandlingService/instagram_event_service.py), so
    # an account with nothing happening costs no CPU, no database traffic and
    # no API calls at all. What remains wakes every six hours to keep the
    # access token fresh and re-assert the webhook subscription, and does
    # nothing whatsoever while nothing is connected.
    instagram_connection_service.start_background_maintenance()
    # Client-Property Matching feature: daily 6 AM IST re-run of the full
    # matching pipeline for every existing client, so properties added since
    # a client last had their requirements changed still get matched against
    # them. Inert until the first 6 AM IST tick, so safe to start unconditionally.
    scheduled_recompute_service.start_daily_recompute_in_background()
    # Site-visit WhatsApp reminder (9 AM IST on the visit day) and next-day
    # follow-up to the client. Sleeps until something is due, so it does not
    # poll the database — see visit_reminder_service's own docstring.
    visit_reminder_service.start_in_background()
    yield
    # Save what the throttled usage writers are still holding, so a clean
    # restart loses none of the Backend / Neon DB tabs' last few minutes.
    for flush_usage in (cpu_usage_service.flush, neon_usage_service.flush):
        try:
            flush_usage()
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"Could not save usage stats on shutdown: {exc!r}")
    # The WhatsApp/Instagram clients above run on daemon threads blocked
    # inside native (cgo) calls into the whatsmeow/neonize Go library —
    # normally daemon threads die the instant the process exits, but a
    # thread parked in native network I/O at this exact moment can leave
    # the OS process itself lingering well after this coroutine returns and
    # uvicorn logs "Application shutdown complete." On Windows that's what
    # turns Ctrl+C into "the terminal never gives back its prompt": the
    # shell is still waiting on a process that looks done but hasn't
    # actually exited. Force-exiting shortly after shutdown guarantees
    # Ctrl+C always hands the prompt back quickly — if shutdown was already
    # clean, the process is gone before this timer ever fires.
    threading.Timer(2.0, lambda: os._exit(0)).start()


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
#
# The public landing page (LandingPage/) is a SECOND Vite app on its own
# port (5174 — pinned there by its vite.config.ts's strictPort so it can
# never drift onto 5173 and collide with the internal frontend). A different
# port is a different origin, so it needs its own entries here or every
# request the public site makes is blocked by the browser before FastAPI
# sees it — the same LAN-IP reasoning as above applies to it too, since the
# site is worth opening on a phone.
#
# Dashboard/ is a THIRD Vite app, on its own pinned port 5175 (see its
# vite.config.ts), same reasoning again.
_cors_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:5175",
    "http://127.0.0.1:5175",
]
if get_settings().frontend_lan_origin:
    _cors_origins.append(get_settings().frontend_lan_origin)
    _cors_origins.append(get_settings().frontend_lan_origin.replace(":5173", ":5174"))

# A ceiling on the handful of endpoints an anonymous stranger can call — see
# Middleware/public_rate_limit.py for exactly which, and why the budgets are
# deliberately far above anything a real visitor could reach.
#
# Added BEFORE the CORS middleware below, which in Starlette means it sits
# INSIDE it (the last middleware added is the outermost one). That ordering
# is load-bearing: a 429 produced here has to travel back out through CORS
# to pick up the Access-Control-Allow-Origin header, or the browser discards
# the response unread and the visitor sees an unexplained failure instead of
# the "try again in a minute" message the body carries.
app.add_middleware(PublicRateLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    # The browser's own HTTP cache reads ETag without this — cache
    # revalidation happens below the layer CORS hides headers at. Exposing
    # it anyway costs nothing and means page code CAN read the tag if it
    # ever needs to do its own conditional request, instead of that being a
    # confusing dead end.
    expose_headers=["ETag"],
    # How long a browser may reuse one preflight result. GET requests no
    # longer trigger a preflight at all (see the API clients: they stopped
    # sending Content-Type on bodyless requests, which is what made a plain
    # GET "non-simple"), so this now covers the writes — where one OPTIONS
    # per 10 minutes is far better than one per save.
    max_age=600,
)

# Property photos are stored as base64 data URLs (Database/models.py's
# PropertyRow.image_urls) and a single property with several photos can put
# a few MB of that text in one response (see landing_page_service.py's own
# comment on this). gzip typically shrinks base64 JSON by ~25-30% — free on
# the server, and the browser decompresses it transparently — so this is
# pure upside for every route, not just the landing page's.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Measures the CPU each request uses, for the Dashboard's Backend tab (see
# Middleware/cpu_meter.py). Added LAST, which in Starlette makes it the
# OUTERMOST middleware, so the time GZip, CORS and the rate limit spend on a
# request is counted as that request's too. It only reads clocks — it never
# changes a request or a response.
app.add_middleware(CpuMeterMiddleware)
install_threadpool_meter()


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Turns FastAPI's default 422 body — a LIST of per-field dicts — into
    the same `{"detail": "<one sentence>"}` shape every HTTPException in this
    application already returns.

    Purely a presentation change; the status code and which requests are
    refused are exactly as before. It exists because the dashboard's API
    client reads `detail` and shows it to the operator as-is (see
    Frontend/src/api/client.ts): with the list it printed a JSON dump of
    pydantic internals, so the careful wording in Model/field_validation.py
    ("That doesn't look like a valid phone number.") never reached the person
    who typed it. The first error is the one shown — the forms validate in
    the browser too, so by the time one gets here there is normally exactly
    one thing wrong.

    "Value error, " is pydantic's own prefix on anything a validator raises;
    stripped so the sentence reads as written."""
    first = (exc.errors() or [{}])[0]
    message = str(first.get("msg") or "").strip()
    if message.startswith("Value error, "):
        message = message[len("Value error, ") :]
    if not message:
        message = "Some of what was sent isn't valid."
    field = [str(part) for part in (first.get("loc") or []) if part not in ("body", "query", "path")]
    # Only for the errors pydantic generated itself (a type/constraint
    # failure names the field but not in a sentence); a validator's own
    # message already says what it is about.
    if field and not message.endswith("."):
        message = f"{field[-1]}: {message}"
    return JSONResponse(status_code=422, content={"detail": message})

# AuthManagement's own routers are the two exceptions to the blanket login
# requirement just below: /api/auth (login must be reachable while logged
# out; /api/auth/me and /api/auth/me/password check the token themselves,
# per-route) and /api/users (every route already requires admin, declared on
# user_controller.router itself).
app.include_router(auth_router, prefix="/api")
app.include_router(user_management_router, prefix="/api")

# Every OTHER router below is the internal ops tool (Frontend/) — nothing an
# anonymous visitor should ever be able to reach. `dependencies=
# [Depends(get_current_user)]` requires a valid, logged-in session for every
# route on that router, without touching a single route handler's own code.
#
# inquiry_form_router and phone_verification_router are NOT included in this
# list: both are genuinely public (see their own module docstrings) — the
# registration form and OTP verification a website visitor uses, with no
# account of their own. Gating them would break the public site
# (LandingPage/) entirely. landing_page_router is similarly public for most
# of its routes, so it is gated per-route instead, inside
# landing_page_controller.py itself, rather than here at the router level.
app.include_router(whatsapp_router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(whatsapp_connections_router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(area_filter_router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(display_settings_router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(property_router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(soldout_property_router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(broker_requirement_router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(builder_project_router, prefix="/api")
app.include_router(whatsapp_inquiry_router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(property_share_router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(inquiry_form_router, prefix="/api")
app.include_router(phone_verification_router, prefix="/api")
app.include_router(matching_router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(requirement_matching_router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(instagram_router, prefix="/api", dependencies=[Depends(get_current_user)])
# NOT gated by get_current_user, and that is deliberate: these are the two
# routes Meta's own servers call (the webhook handshake and the event
# deliveries) plus the OAuth redirect the browser returns on from
# instagram.com. None of them can carry a login token. Each authenticates
# the caller in its own way instead — a shared verify token, an HMAC-SHA256
# body signature, and a one-time OAuth state respectively. See
# Controller/InstagramInquiryHandlingController/instagram_webhook_controller.py.
app.include_router(instagram_webhook_router, prefix="/api")
# The Dashboard's usage endpoints: open by default, gated by DASHBOARD_KEY
# once one is set (see Middleware/dashboard_access.py) — the Message to Model
# feed carries raw WhatsApp text, so set it before hosting. area_knowledge_router
# lives here too, not in the get_current_user group above: it's the Surat
# Area Knowledge Base tab, which is part of the Dashboard app (no login of
# its own) rather than the JWT-authenticated Frontend ops tool.
app.include_router(llm_usage_router, prefix="/api", dependencies=[Depends(require_dashboard_key)])
app.include_router(neon_usage_router, prefix="/api", dependencies=[Depends(require_dashboard_key)])
app.include_router(backend_usage_router, prefix="/api", dependencies=[Depends(require_dashboard_key)])
app.include_router(area_knowledge_router, prefix="/api", dependencies=[Depends(require_dashboard_key)])
app.include_router(landing_page_router, prefix="/api")
app.include_router(agent_router, prefix="/api", dependencies=[Depends(get_current_user)])


@app.get("/")
def root() -> dict:
    return {"service": "real-estate-whatsapp-ingestion", "status": "running"}
