"""FastAPI entrypoint.

Run with:
    uvicorn main:app --reload --port 8000

On startup, spawns the WhatsApp client (Service/WhatsAppDataFetchingService/whatsapp_client.py) on a
background thread. It prints its own progress (pairing, group/personal-chat
selection, incoming messages) straight to this terminal — see
Middleware/step_logger.py.
"""

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
from Controller.WhatsAppInquiryHandlingController.whatsapp_inquiry_controller import router as whatsapp_inquiry_router
from Database.client_session import init_client_db, is_client_database_configured
from Database.session import init_db, is_database_configured
from Middleware.logging_config import configure_logging
from Middleware import step_logger
from Service.WhatsAppDataFetchingService import area_filter_service, display_settings_service, duplicate_detection_service, whatsapp_service
from Service.WhatsAppInquiryHandlingService import whatsapp_inquiry_service

configure_logging()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if is_database_configured():
        step_logger.step("DATABASE_URL is set — initializing the database and loading saved settings...")
        init_db()
        area_filter_service.load_from_database()
        display_settings_service.load_from_database()
        duplicate_detection_service.load_from_database()
        step_logger.success("Database ready — properties and settings will persist across restarts.")
    else:
        step_logger.info(
            "DATABASE_URL is not set — running with in-memory storage only. Nothing is lost while the "
            "server stays up, but properties and settings reset on restart until a database is connected."
        )

    if is_client_database_configured():
        step_logger.step("CLIENT_DATABASE_URL is set — initializing the client database...")
        init_client_db()
        step_logger.success("Client database ready — inquiry-handling client records will persist across restarts.")
    else:
        step_logger.info(
            "CLIENT_DATABASE_URL is not set — whatsappInquiryHandling client records will use in-memory "
            "storage only until it's configured."
        )

    step_logger.step("FastAPI server is up. Launching WhatsApp client in the background...")
    whatsapp_service.start_agent_in_background()
    whatsapp_inquiry_service.start_agent_in_background()
    yield


app = FastAPI(title="Real Estate WhatsApp Ingestion API", lifespan=lifespan)

# The frontend (Frontend/, Vite dev server) runs on a different origin than
# this API, so without CORS the browser blocks every request from it — a
# failure curl/pytest would never catch, only a real browser would. An
# explicit localhost allow-list, not "*": this is dev-only for now, and the
# production frontend origin gets added here once it's hosted (Step 12).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
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


@app.get("/")
def root() -> dict:
    return {"service": "real-estate-whatsapp-ingestion", "status": "running"}
