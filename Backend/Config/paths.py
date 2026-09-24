"""Where this process keeps the files it writes while it runs — the one
place every runtime-written path is derived from.

Runtime state (pending batches, the area knowledge base, the usage stats and
the WhatsApp session files) is not source code: it is created and rewritten
by the running server, and it has to be somewhere that outlives the process.

  - Locally (DATA_DIR unset): DATA_DIR is the PROJECT ROOT, one level above
    Backend/, exactly where these folders have always lived. They must stay
    outside Backend/ there, because uvicorn --reload watches Backend/ and
    would restart the server — killing the live WhatsApp connections — on
    every write inside it.
  - On Railway (DATA_DIR=/data, the mount path of a Volume): everything is
    on the Volume, which survives restarts and redeploys. Without a Volume,
    a redeploy starts from a brand new disk and all of it would be lost.

The WhatsApp session files are the one exception to "always under DATA_DIR":
they only move there when DATA_DIR is explicitly set. Unset, they stay in
their original folders under Backend/Service/ (see whatsapp_connection_manager),
so a local setup keeps its existing pairings untouched.
"""

from __future__ import annotations

from pathlib import Path

from Config.settings import get_settings

# Backend/Config/this_file.py
#   parents[0] = .../Backend/Config
#   parents[1] = .../Backend
#   parents[2] = .../<project root>
BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent


def _resolve_data_dir() -> tuple[Path, bool]:
    raw = (get_settings().data_dir or "").strip()
    if not raw:
        return PROJECT_ROOT, False
    path = Path(raw).expanduser()
    if not path.is_absolute():
        # Relative to the project root rather than the working directory, so
        # the same value means the same folder however the server is started.
        path = PROJECT_ROOT / path
    return path.resolve(), True


DATA_DIR, DATA_DIR_IS_SET = _resolve_data_dir()
