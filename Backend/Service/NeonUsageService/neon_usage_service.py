"""What actually spends Neon's CU-hours and Network Transfer — measured
locally, for the Dashboard's "Neon DB" tab.

THE POINT: Neon's own dashboard shows the totals accurately but never says
WHICH operation caused them. This module answers that, and it does so
WITHOUT asking Neon anything: every number here is measured on this side of
the wire, from the queries the app itself runs, and is kept in memory +
a plain JSON file OUTSIDE Backend/ (same reasoning as
Service/WhatsAppDataFetchingService/area_knowledge_service.py: uvicorn's
--reload watches every *.py under Backend/, so a runtime-written file has
to live one directory up or the server restarts — and drops live WhatsApp
connections — on every write).

That is deliberate and load-bearing, not an implementation detail: reading
this tab must not itself cost CU-hours or transfer, or watching the cost
would raise the cost, which would produce more rows to watch. There is no
such loop here — the read path (get_overview) touches memory only, the
write path touches a local file only, and nothing in this module ever opens
a database connection or runs a query of its own.

HOW CU-HOURS ACTUALLY ACCRUE (the thing that is hard to see from Neon's
graph): Neon bills compute for the time it is AWAKE, not for the time it
spends executing. An idle compute suspends after AUTOSUSPEND_SECONDS, so a
single 20 ms query that arrives while it is asleep wakes it and bills the
full idle tail behind it — roughly 0.021 CU-hours at the defaults below,
for one query. Twenty such scattered queries cost far more than one busy
minute does. So compute is grouped here into ACTIVITY WINDOWS: a run of
queries with no gap longer than the autosuspend delay is one wake-up, and
one wake-up is one row on the tab.

NETWORK TRANSFER is the opposite shape — it is pure data volume, so it is
attributed per operation, from the real byte size of each result set (read
off psycopg's own PGresult, so it is what actually crossed the wire, not a
guess).

NOISE CONTROL: every query is counted, but they are not all listed. Inside
one window, operations are listed individually while they are worth looking
at (a real share of that window, or over a fixed floor) and everything else
collapses into ONE "smaller operations" line that states how many there
were and what they cost together. Nothing is hidden and nothing is
double-counted: the lines always add back up to the window's own totals.

ASSUMPTIONS are constants below, not settings, and are shown on the tab so
they can be checked against the Neon console. Change them there if the
project's Neon plan differs.

SAFETY: the two SQLAlchemy listeners run inside every single query this app
makes, on whichever thread made it. They are wrapped so they can never
raise into a real query, the per-query work is bounded (see
_MAX_CELLS_SCANNED), and the file is written at most once every
_PERSIST_EVERY_SECONDS rather than per query.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from Middleware import step_logger
from Service.BackendUsageService import usage_feed

# Backend/Service/NeonUsageService/this_file.py -> parents[2] = Backend/
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_PROJECT_ROOT = _BACKEND_DIR.parent
_USAGE_DIR = _PROJECT_ROOT / "NeonUsage"
_USAGE_PATH = _USAGE_DIR / "neon_usage_stats.json"

# ---------------------------------------------------------------- assumptions
# Neon's defaults on the free tier. Both are shown on the tab so they can be
# checked against the Neon console; change them here if the plan differs.
COMPUTE_UNITS = 0.25
AUTOSUSPEND_SECONDS = 300.0

# A gap at least this long means the compute had suspended, so the next
# query is a fresh wake-up and starts a new window. Same number as the
# autosuspend delay, by definition.
_WINDOW_GAP_SECONDS = AUTOSUSPEND_SECONDS

# How much history is kept: the last 48 hours — the Dashboard's window, see
# usage_feed.RETENTION_SECONDS — with a count cap as a backstop. A new
# window only starts after a gap longer than the autosuspend delay, so 48
# hours can hold at most 576 of them: 600 never cuts into the 48 hours.
_WINDOW_LIMIT = 600
_RETENTION_SECONDS = usage_feed.RETENTION_SECONDS
_OPS_PER_WINDOW_LIMIT = 60

# Bounded work per query when measuring a result's real byte size: a huge
# result is sampled and extrapolated rather than walked cell by cell.
_MAX_CELLS_SCANNED = 20_000

_PERSIST_EVERY_SECONDS = 20.0

# How many rows each tab shows at most, and when an operation stops being
# worth its own line (see the module docstring's NOISE CONTROL).
_MAX_ROWS = 120
_KEEP_OPS_PER_WINDOW = 6
_KEEP_MIN_SHARE = 0.05
_KEEP_MIN_MILLISECONDS = 250.0
_KEEP_MIN_BYTES = 100 * 1024

_lock = threading.RLock()
_windows: Deque[Dict[str, Any]] = deque(maxlen=_WINDOW_LIMIT)
_last_persist_at = 0.0
_dirty = False
_loaded = False
_attached = False
_warned_once = False
# Bumped on every change to any window, and stamped on that window as "v" —
# what lets get_changes() hand the dashboard only the windows that moved.
# Starts at 1: a window without a stamp counts as 1, so it goes out in a full
# sync but never again to a cursor this process has already issued.
_version = 1


# ------------------------------------------------------------------ utilities


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _blank_counters() -> Dict[str, Any]:
    return {"queries": 0, "ms": 0.0, "bytes": 0, "rows": 0}


def _add(into: Dict[str, Any], queries: int, ms: float, size: int, rows: int) -> None:
    into["queries"] += queries
    into["ms"] += ms
    into["bytes"] += size
    into["rows"] += rows


def _fmt_bytes(size: float) -> str:
    if size < 1024:
        return f"{int(size)} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.2f} GB"


def _fmt_seconds(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes = int(seconds // 60)
    rest = int(seconds % 60)
    return f"{minutes} min {rest} s" if rest else f"{minutes} min"


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def _humanize(name: str) -> str:
    cleaned = name.strip("_").replace("_", " ").strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned else ""


def _area_from_module(module: str) -> str:
    for suffix in ("_repository", "_store", "_service", "_controller", "_session"):
        if module.endswith(suffix):
            module = module[: -len(suffix)]
            break
    return _humanize(module) or "Database"


# --------------------------------------------------------- naming an operation


def _module_of(path: str) -> str:
    return path.rsplit("/", 1)[-1].removesuffix(".py")


def _short_sql(statement: str) -> str:
    flat = " ".join((statement or "").split())
    return flat[:70] + ("…" if len(flat) > 70 else "")


def _describe_caller(statement: str) -> Tuple[str, str, str, str]:
    """(operation label, area, trigger label, trigger kind) for the query
    about to run, read off this thread's own call stack — no database
    involved.

    The OPERATION is the innermost Database/ frame (the repository function
    doing the work). The TRIGGER is the outermost Controller/Service/Agent
    frame, which is what actually set this off — a page calling the API, or
    a background pipeline — because "which screen/job costs me this" is the
    question this tab exists to answer."""
    operation: Optional[Tuple[str, str]] = None
    trigger: Optional[Tuple[str, str, bool]] = None
    try:
        frame: Any = sys._getframe(1)
        depth = 0
        while frame is not None and depth < 45:
            path = frame.f_code.co_filename.replace("\\", "/")
            name = frame.f_code.co_name
            if "/sqlalchemy/" not in path and "/neon_usage_service.py" not in path:
                if "/Database/" in path:
                    if operation is None:
                        operation = (_module_of(path), name)
                elif "/Controller/" in path:
                    # Walking inner -> outer, so the last match wins, which
                    # is the outermost frame: the real entry point.
                    trigger = (_module_of(path), name, True)
                elif "/Service/" in path or "/Agent/" in path:
                    trigger = (_module_of(path), name, False)
            frame = frame.f_back
            depth += 1
    except Exception:  # noqa: BLE001 - naming must never break a real query
        pass

    if operation is not None:
        label = _humanize(operation[1])
        area = _area_from_module(operation[0])
    else:
        flat = " ".join((statement or "").split()).upper()
        if flat.startswith("SELECT 1"):
            # pool_pre_ping's health check before handing out a pooled
            # connection — tiny, but it is real traffic and it can be the
            # thing that wakes a sleeping compute, so it is named, not hidden.
            label, area = "Connection health check", "Connection"
        else:
            label, area = _short_sql(statement), "Database"

    if trigger is not None:
        trigger_label = _humanize(trigger[1])
        trigger_kind = "API request" if trigger[2] else "Background job"
    else:
        trigger_label = "Startup / maintenance"
        trigger_kind = "Startup"
    return label, area, trigger_label, trigger_kind


def _result_size(cursor: Any) -> Tuple[int, int]:
    """(bytes, rows) that actually came back over the wire for this
    statement, read from psycopg's own PGresult — this is the number Neon
    counts as Network Transfer, not an estimate of it. Returns (0, rows) on
    any driver that doesn't expose it (e.g. SQLite in tests)."""
    try:
        result = getattr(cursor, "pgresult", None)
        if result is None:
            return 0, max(0, int(getattr(cursor, "rowcount", 0) or 0))
        rows = int(result.ntuples)
        cols = int(result.nfields)
        if rows <= 0 or cols <= 0:
            return 0, max(0, rows)
        scan_rows = rows if rows * cols <= _MAX_CELLS_SCANNED else max(1, _MAX_CELLS_SCANNED // cols)
        total = 0
        get_value = result.get_value
        for row_index in range(scan_rows):
            for col_index in range(cols):
                value = get_value(row_index, col_index)
                if value is not None:
                    total += len(value)
        if scan_rows < rows:
            total = int(total * (rows / scan_rows))
        return total, rows
    except Exception:  # noqa: BLE001
        return 0, 0


# --------------------------------------------------------------- the write path


def record_query(label: str, area: str, trigger: str, trigger_kind: str, ms: float, size: int, rows: int) -> None:
    """Files one executed statement into its activity window. Never raises."""
    global _dirty, _last_persist_at, _version
    try:
        now = time.time()
        with _lock:
            if not _loaded:
                load_from_disk()
            window = _windows[-1] if _windows else None
            if window is None or now - window["last_at"] > _WINDOW_GAP_SECONDS:
                # A new wake-up is the moment to let go of any older than the
                # 48 hours the Dashboard shows.
                while _windows and now - _windows[0]["last_at"] > _RETENTION_SECONDS:
                    _windows.popleft()
                window = {
                    "started_at": now,
                    "last_at": now,
                    "trigger": trigger,
                    "trigger_kind": trigger_kind,
                    "ops": {},
                    "other_ops": _blank_counters(),
                    **_blank_counters(),
                }
                _windows.append(window)
            window["last_at"] = now
            _add(window, 1, ms, size, rows)
            _version += 1
            window["v"] = _version

            ops = window["ops"]
            counters = ops.get(label)
            if counters is None:
                if len(ops) >= _OPS_PER_WINDOW_LIMIT:
                    # A window with this many distinct operations is a bulk
                    # job; the tail goes to the folded line rather than
                    # growing the file without bound.
                    _add(window["other_ops"], 1, ms, size, rows)
                    counters = None
                else:
                    counters = {**_blank_counters(), "area": area}
                    ops[label] = counters
            if counters is not None:
                _add(counters, 1, ms, size, rows)

            _dirty = True
            due = now - _last_persist_at >= _PERSIST_EVERY_SECONDS
            if due:
                _last_persist_at = now
                _persist_locked()
    except Exception as exc:  # noqa: BLE001
        _warn_once(f"Could not record Neon usage for a query: {exc!r}")


def _warn_once(message: str) -> None:
    global _warned_once
    if not _warned_once:
        _warned_once = True
        step_logger.warn(f"{message} (Neon usage tracking is off for the rest of this run; the app is unaffected.)")


# ------------------------------------------------------------------ persistence


def _atomic_write(path: Path, text: str) -> None:
    _USAGE_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def _persist_locked() -> None:
    """Caller holds the lock. Throttled by record_query — never per query."""
    global _dirty
    try:
        _atomic_write(_USAGE_PATH, json.dumps({"windows": list(_windows)}, ensure_ascii=False))
        _dirty = False
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Could not write the Neon usage file ({_USAGE_PATH}): {exc!r}")


def flush() -> None:
    """Writes anything the throttle is still holding. Called by get_overview
    so what the tab shows is also what survives a restart."""
    with _lock:
        if _dirty:
            _persist_locked()


def load_from_disk() -> None:
    """Called once at startup (Backend/main.py). Never raises — an unreadable
    file starts this process empty and is left untouched on disk."""
    global _loaded, _version
    with _lock:
        if _USAGE_PATH.exists():
            try:
                raw = json.loads(_USAGE_PATH.read_text(encoding="utf-8"))
                floor = time.time() - _RETENTION_SECONDS
                for window in (raw or {}).get("windows", []):
                    if not isinstance(window, dict) or "started_at" not in window:
                        continue
                    window.setdefault("last_at", window["started_at"])
                    if float(window["last_at"]) < floor:
                        continue  # older than the 48 hours the Dashboard shows
                    window.setdefault("other_ops", _blank_counters())
                    window.setdefault("ops", {})
                    # New to every cursor this process hands out (get_changes).
                    window["v"] = 1
                    _windows.append(window)
                _version = max(_version, 1)
            except Exception as exc:  # noqa: BLE001
                step_logger.error(
                    f"The Neon usage file ({_USAGE_PATH}) could not be read: {exc!r}. Starting empty; "
                    "the file is left as it is until the next successful write."
                )
        _loaded = True
        step_logger.info(f"Neon usage history loaded: {len(_windows)} wake-up(s) recorded ({_USAGE_PATH}).")


# ----------------------------------------------------------------- attachment


def attach_to_engine(engine: Any) -> None:
    """Wires the two SQLAlchemy listeners onto the one shared engine. Called
    from Database/session.py right after the engine is created, and wrapped
    there too — instrumentation must never be able to stop the database from
    working."""
    global _attached
    from sqlalchemy import event

    if _attached:
        return

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ARG001
        try:
            context._neon_started = time.perf_counter()
        except Exception:  # noqa: BLE001
            pass

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ARG001
        try:
            started = getattr(context, "_neon_started", None)
            ms = (time.perf_counter() - started) * 1000.0 if started else 0.0
            size, rows = _result_size(cursor)
            label, area, trigger, trigger_kind = _describe_caller(statement)
            record_query(label, area, trigger, trigger_kind, ms, size, rows)
        except Exception as exc:  # noqa: BLE001
            _warn_once(f"Could not measure a query for Neon usage: {exc!r}")

    _attached = True
    step_logger.info("Neon usage tracking attached — every query is now measured locally (no extra database calls).")


# ------------------------------------------------------------------ the read path


def _fold(
    ops: Dict[str, Dict[str, Any]],
    value_key: str,
    window_total: float,
    overflow: Dict[str, Any],
    min_value: float,
) -> Tuple[List[Tuple[str, Dict[str, Any]]], Dict[str, Any], int]:
    """Splits one window's operations into "worth its own line" and "folded
    away", so a window never renders as fifty near-identical rows and never
    silently drops what those rows cost. The folded counters always carry
    the remainder exactly, so kept + folded == the window's own totals."""
    ranked = sorted(ops.items(), key=lambda item: item[1].get(value_key, 0) or 0, reverse=True)
    kept: List[Tuple[str, Dict[str, Any]]] = []
    folded = dict(_blank_counters())
    overflow_queries = int(overflow.get("queries", 0) or 0)
    _add(
        folded,
        overflow_queries,
        float(overflow.get("ms", 0.0) or 0.0),
        int(overflow.get("bytes", 0) or 0),
        int(overflow.get("rows", 0) or 0),
    )
    folded_labels = 1 if overflow_queries else 0
    for label, counters in ranked:
        value = counters.get(value_key, 0) or 0
        worth_a_line = (window_total > 0 and value >= window_total * _KEEP_MIN_SHARE) or value >= min_value
        if len(kept) < _KEEP_OPS_PER_WINDOW and worth_a_line and value > 0:
            kept.append((label, counters))
        else:
            _add(folded, counters["queries"], counters["ms"], counters["bytes"], counters["rows"])
            folded_labels += 1
    return kept, folded, folded_labels


def _share(value: float, total: float) -> float:
    return round((value / total) * 100, 1) if total > 0 else 0.0


def get_overview() -> Dict[str, Any]:
    """Everything the Neon DB tab shows. Reads memory only — this call makes
    no database query, so opening or refreshing the tab costs no CU-hours
    and no transfer."""
    flush()
    with _lock:
        windows = [dict(window) for window in _windows]
    now = time.time()

    total_cu = 0.0
    total_bytes = 0
    total_queries = 0
    total_awake = 0.0
    for window in windows:
        billed = max(0.0, window["last_at"] - window["started_at"]) + AUTOSUSPEND_SECONDS
        total_awake += billed
        total_cu += billed * COMPUTE_UNITS / 3600.0
        total_bytes += int(window.get("bytes", 0))
        total_queries += int(window.get("queries", 0))

    compute_rows: List[Dict[str, Any]] = []
    transfer_rows: List[Dict[str, Any]] = []

    for window in reversed(windows):
        if len(compute_rows) < _MAX_ROWS:
            row = _compute_row(window, now)
            row["share_percent"] = _share(_window_cu_hours(window), total_cu)
            compute_rows.append(row)
        if int(window.get("bytes", 0)) > 0 and len(transfer_rows) < _MAX_ROWS:
            for row in _transfer_rows(window):
                row["share_percent"] = _share(row["bytes"], total_bytes)
                transfer_rows.append(row)

    return {
        "assumptions": {
            "compute_units": COMPUTE_UNITS,
            "autosuspend_seconds": AUTOSUSPEND_SECONDS,
            "tracking_since": _iso(windows[0]["started_at"]) if windows else None,
        },
        "totals": {
            "wakeups": len(windows),
            "queries": total_queries,
            "cu_hours": round(total_cu, 5),
            "awake_seconds": round(total_awake, 1),
            "bytes": total_bytes,
            "bytes_text": _fmt_bytes(total_bytes),
        },
        "compute": compute_rows,
        "transfer": transfer_rows,
    }


def get_changes(cursor: Optional[str]) -> Dict[str, Any]:
    """The Dashboard's hourly Neon view: every wake-up of the last 48 hours
    that changed after `cursor` (all of them for a new or other-process
    cursor), each rendered into its CU-hours row and Network Transfer rows
    WITHOUT share percentages — the page works those out against the 48
    hours it shows. Memory only, like get_overview."""
    since = usage_feed.parse_cursor(cursor)
    now = time.time()
    with _lock:
        position = _version
        changed = [
            {
                **window,
                "ops": {label: dict(counters) for label, counters in window.get("ops", {}).items()},
                "other_ops": dict(window.get("other_ops") or _blank_counters()),
            }
            for window in _windows
            if window.get("v", 1) > since and now - window["last_at"] <= _RETENTION_SECONDS
        ]
    return {
        "cursor": usage_feed.make_cursor(position),
        "items": [
            {
                "k": f"{window['started_at']:.6f}",
                "compute": _compute_row(window, now),
                "transfer": _transfer_rows(window) if int(window.get("bytes", 0)) > 0 else [],
            }
            for window in changed
        ],
        "assumptions": {
            "compute_units": COMPUTE_UNITS,
            "autosuspend_seconds": AUTOSUSPEND_SECONDS,
            "tracking_since": None,
        },
    }


def _window_cu_hours(window: Dict[str, Any]) -> float:
    active = max(0.0, window["last_at"] - window["started_at"])
    return (active + AUTOSUSPEND_SECONDS) * COMPUTE_UNITS / 3600.0


def _compute_row(window: Dict[str, Any], now: float) -> Dict[str, Any]:
    """One wake-up's CU-hours row, without share_percent."""
    active = max(0.0, window["last_at"] - window["started_at"])
    billed = active + AUTOSUSPEND_SECONDS
    cu_hours = billed * COMPUTE_UNITS / 3600.0
    queries = int(window.get("queries", 0))
    still_open = (now - window["last_at"]) <= AUTOSUSPEND_SECONDS
    ops = window.get("ops", {})
    overflow = window.get("other_ops", _blank_counters())
    kept, folded, folded_labels = _fold(ops, "ms", window.get("ms", 0.0), overflow, _KEEP_MIN_MILLISECONDS)
    operations = [
        {
            "label": label,
            "area": counters.get("area", "Database"),
            "queries": counters["queries"],
            "seconds": round(counters["ms"] / 1000.0, 3),
            "bytes": counters["bytes"],
            "summary": (
                f"{label} — {counters['queries']} {_plural(counters['queries'], 'query', 'queries')}, "
                f"{_fmt_seconds(counters['ms'] / 1000.0)} of database time"
            ),
        }
        for label, counters in kept
    ]
    if folded["queries"]:
        operations.append(
            {
                "label": f"{folded_labels} smaller {_plural(folded_labels, 'operation', 'operations')}",
                "area": "Everything else",
                "queries": folded["queries"],
                "seconds": round(folded["ms"] / 1000.0, 3),
                "bytes": folded["bytes"],
                "summary": (
                    f"{folded_labels} smaller {_plural(folded_labels, 'operation', 'operations')} — "
                    f"{folded['queries']} {_plural(folded['queries'], 'query', 'queries')}, "
                    f"{_fmt_seconds(folded['ms'] / 1000.0)} of database time combined"
                ),
            }
        )
    return {
        "at": _iso(window["started_at"]),
        "ended_at": _iso(window["last_at"]),
        "still_awake": still_open,
        "trigger": window.get("trigger", "Startup / maintenance"),
        "trigger_kind": window.get("trigger_kind", "Startup"),
        "queries": queries,
        "active_seconds": round(active, 3),
        "idle_tail_seconds": AUTOSUSPEND_SECONDS,
        "billed_seconds": round(billed, 3),
        "cu_hours": round(cu_hours, 5),
        "summary": (
            f"Woke the database — {queries} {_plural(queries, 'query', 'queries')} over "
            f"{_fmt_seconds(active)}, then it must stay awake {_fmt_seconds(AUTOSUSPEND_SECONDS)} "
            f"more before it can sleep. That is {_fmt_seconds(billed)} of compute billed "
            f"= {cu_hours:.4f} CU-hours."
        ),
        "operations": operations,
    }


def _transfer_rows(window: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One wake-up's Network Transfer rows, without share_percent."""
    ops = window.get("ops", {})
    overflow = window.get("other_ops", _blank_counters())
    kept, folded, folded_labels = _fold(ops, "bytes", int(window.get("bytes", 0)), overflow, _KEEP_MIN_BYTES)
    rows: List[Dict[str, Any]] = [
        {
            "at": _iso(window["last_at"]),
            "trigger": window.get("trigger", "Startup / maintenance"),
            "trigger_kind": window.get("trigger_kind", "Startup"),
            "label": label,
            "area": counters.get("area", "Database"),
            "queries": counters["queries"],
            "bytes": counters["bytes"],
            "rows": counters["rows"],
            "summary": (
                f"{label} — {_fmt_bytes(counters['bytes'])} downloaded over "
                f"{counters['queries']} {_plural(counters['queries'], 'query', 'queries')} "
                f"({counters['rows']} {_plural(counters['rows'], 'row', 'rows')})"
            ),
        }
        for label, counters in kept
    ]
    if folded["bytes"] > 0:
        rows.append(
            {
                "at": _iso(window["last_at"]),
                "trigger": window.get("trigger", "Startup / maintenance"),
                "trigger_kind": window.get("trigger_kind", "Startup"),
                "label": f"{folded_labels} smaller {_plural(folded_labels, 'operation', 'operations')}",
                "area": "Everything else",
                "queries": folded["queries"],
                "bytes": folded["bytes"],
                "rows": folded["rows"],
                "summary": (
                    f"{folded_labels} smaller {_plural(folded_labels, 'operation', 'operations')} — "
                    f"{_fmt_bytes(folded['bytes'])} combined over {folded['queries']} "
                    f"{_plural(folded['queries'], 'query', 'queries')}. Individually tiny, which is "
                    "exactly how transfer creeps up unnoticed."
                ),
            }
        )
    return rows
