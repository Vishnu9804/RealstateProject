"""FastAPI entrypoint.

Run with:
    uvicorn main:app --reload --port 8000

On startup, spawns the WhatsApp client (Service/whatsapp_client.py) on a
background thread. It prints its own progress (pairing, group/personal-chat
selection, incoming messages) straight to this terminal — see
Middleware/step_logger.py.
"""

import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

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

from Controller.area_filter_controller import router as area_filter_router
from Controller.display_settings_controller import router as display_settings_router
from Controller.property_controller import router as property_router
from Controller.whatsapp_controller import router as whatsapp_router
from Middleware.logging_config import configure_logging
from Middleware import step_logger
from Service import whatsapp_service

configure_logging()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    step_logger.step("FastAPI server is up. Launching WhatsApp client in the background...")
    whatsapp_service.start_agent_in_background()
    yield


app = FastAPI(title="Real Estate WhatsApp Ingestion API", lifespan=lifespan)
app.include_router(whatsapp_router, prefix="/api")
app.include_router(area_filter_router, prefix="/api")
app.include_router(display_settings_router, prefix="/api")
app.include_router(property_router, prefix="/api")


@app.get("/")
def root() -> dict:
    return {"service": "real-estate-whatsapp-ingestion", "status": "running"}
