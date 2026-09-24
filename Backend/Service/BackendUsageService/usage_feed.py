"""Shared plumbing for the Dashboard's hour-by-hour usage feeds (the LLM
Cost, Neon DB, Backend vCPU and Message to Model tabs).

HOW A FEED WORKS — AND WHY IT IS SHAPED LIKE THIS

The dashboard is a static page that keeps its own copy of the last 48 hours
in the browser (IndexedDB). It never asks for "everything" twice: every
response carries a CURSOR, the page sends it back on its next poll, and the
answer is only what changed after it. A poll with nothing new is a few dozen
bytes and a dictionary scan — no database, no disk read, and no re-sending of
data the page already holds. Each entry crosses the wire once (or once more
per update, for an hour bucket that is still filling up).

A cursor is "<boot id>.<position>". Positions only mean something to the
process that issued them, so a cursor from before a restart (or a redeploy,
which also wipes these files if DATA_DIR is not on a persistent Volume) is
answered with everything still held — the page merges by key, so a resync can never double count.

Items that are running totals (one hour's CPU for one endpoint, one hour's
tokens for one model) are keyed with the EPOCH of the store that produced
them. The epoch is saved alongside that store's file and only changes when
the store starts from empty — so after a redeploy that lost the file, the new
process's counters start at zero under a new epoch and the page ADDS them to
what it already had for that hour, rather than overwriting its larger
pre-restart numbers with smaller ones.

TIME: every bucket is an IST hour. UTC+5:30 has no daylight saving, so a
fixed offset is exact — and an IST hour starts at :30 UTC, which is why
nothing here uses a plain UTC hour.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
from pathlib import Path
from typing import Any, Optional, Tuple

from fastapi import Response

from Config import paths

# Runtime-written files live under DATA_DIR (Config/paths.py): locally the
# project root, one level ABOVE Backend/, so uvicorn's --reload (which
# watches Backend/) never restarts the server over an ordinary stats write —
# the same reasoning as llm_usage_service and neon_usage_service. On Railway
# it is the Volume, so the files survive a redeploy.
DATA_DIR = paths.DATA_DIR

RETENTION_SECONDS = 48 * 3600
_IST_OFFSET_SECONDS = 5 * 3600 + 30 * 60

# Identifies this process in every cursor it hands out (see the docstring).
BOOT_ID = secrets.token_hex(4)


def ist_hour_start(epoch_seconds: float) -> int:
    """Unix time of the start of the IST hour `epoch_seconds` falls in."""
    return int((epoch_seconds + _IST_OFFSET_SECONDS) // 3600) * 3600 - _IST_OFFSET_SECONDS


def retention_floor(now: float) -> int:
    """Start of the oldest IST hour still inside the 48-hour window — the
    current hour plus the 47 before it."""
    return ist_hour_start(now) - (RETENTION_SECONDS - 3600)


def new_epoch() -> str:
    return secrets.token_hex(6)


def make_cursor(position: int) -> str:
    return f"{BOOT_ID}.{int(position)}"


def parse_cursor(cursor: Optional[str]) -> int:
    """The position to send changes after — 0 (everything) for a missing,
    malformed or other-process cursor."""
    if not cursor:
        return 0
    boot, _, position = cursor.partition(".")
    if boot != BOOT_ID:
        return 0
    try:
        return max(0, int(position))
    except ValueError:
        return 0


def atomic_write(path: Path, text: str) -> None:
    """Write-then-rename, so a reader (or a crash) never sees a half-written
    file. The temp name is per thread, so two writers can never collide."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def json_response(payload: Any) -> Response:
    """Compact JSON straight from json.dumps — skips FastAPI's per-field
    encoder walk, which is real CPU on a feed of a few hundred items and buys
    nothing for payloads that are plain dicts already. no-store: a delta
    answer is only valid for the cursor it was asked with."""
    return Response(
        content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


# ------------------------------------------------------------------ memory


def read_process_memory() -> Tuple[Optional[int], Optional[int]]:
    """(current RSS, peak RSS) of this process in bytes, straight from the
    OS. None for whatever the platform cannot say. Never raises."""
    try:
        if sys.platform.startswith("linux"):
            rss: Optional[int] = None
            peak: Optional[int] = None
            with open("/proc/self/status", "rb") as handle:
                for line in handle:
                    if line.startswith(b"VmRSS:"):
                        rss = int(line.split()[1]) * 1024
                    elif line.startswith(b"VmHWM:"):
                        peak = int(line.split()[1]) * 1024
            return rss, peak
        if sys.platform == "win32":
            return _windows_process_memory()
        import resource  # macOS: only the peak is available, already in bytes

        return None, int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:  # noqa: BLE001 - measuring must never break a caller
        return None, None


def read_container_memory() -> Tuple[Optional[int], Optional[int]]:
    """(working set, limit) of the Linux container this process runs in, in
    bytes — the figure a container platform (Railway included) reports as
    the service's memory. Working set = the cgroup's usage minus its
    inactive file cache, the same definition cAdvisor/Kubernetes use. None
    outside a cgroup (e.g. local Windows development). Never raises."""
    if not sys.platform.startswith("linux"):
        return None, None
    candidates = (
        ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.stat", b"inactive_file "),
        (
            "/sys/fs/cgroup/memory/memory.usage_in_bytes",
            "/sys/fs/cgroup/memory/memory.limit_in_bytes",
            "/sys/fs/cgroup/memory/memory.stat",
            b"total_inactive_file ",
        ),
    )
    for current_path, limit_path, stat_path, inactive_key in candidates:
        try:
            with open(current_path, "rb") as handle:
                current = int(handle.read().strip())
        except (OSError, ValueError):
            continue
        inactive = 0
        try:
            with open(stat_path, "rb") as handle:
                for line in handle:
                    if line.startswith(inactive_key):
                        inactive = int(line.split()[1])
                        break
        except (OSError, ValueError, IndexError):
            inactive = 0
        limit: Optional[int] = None
        try:
            with open(limit_path, "rb") as handle:
                raw = handle.read().strip()
            if raw != b"max":
                value = int(raw)
                # cgroup v1 reports "no limit" as a huge sentinel number.
                if 0 < value < (1 << 50):
                    limit = value
        except (OSError, ValueError):
            limit = None
        return max(0, current - inactive), limit
    return None, None


def billable_memory() -> Optional[int]:
    """The best available figure for what the platform bills as this
    service's memory: the container working set when there is one, else the
    process RSS."""
    container, _limit = read_container_memory()
    if container is not None:
        return container
    rss, _peak = read_process_memory()
    return rss


_windows_counters_type: Any = None


def _windows_process_memory() -> Tuple[Optional[int], Optional[int]]:
    global _windows_counters_type
    import ctypes
    from ctypes import wintypes

    if _windows_counters_type is None:

        class _ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi")
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        _windows_counters_type = (_ProcessMemoryCounters, kernel32, psapi)

    counters_type, kernel32, psapi = _windows_counters_type
    counters = counters_type()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None, None
    return int(counters.WorkingSetSize), int(counters.PeakWorkingSetSize)
