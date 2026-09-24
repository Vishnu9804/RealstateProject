"""What spends the backend's vCPU — measured locally, for the Dashboard's
Backend tab (vCPU capsule).

Railway bills a container for the CPU time it actually uses, so the question
is "which request or job used it?". Two kinds of number are kept, and kept
apart on purpose:

  - The PROCESS total per IST hour is exact: time.process_time() is the
    kernel's own count of CPU seconds used by every thread of this process —
    Python threads, the WhatsApp (neonize/whatsmeow) Go runtime, PyTorch's
    native threads, the garbage collector. That is the number Railway bills.
    The container's memory is sampled alongside it, so each hour also knows
    its average RAM (and therefore its RAM cost).
  - The ENTRIES say what that time was spent on, measured with
    time.thread_time() inside the thread doing the work: every HTTP request
    (Middleware/cpu_meter.py) and every background job wearing @tracked
    below (a WhatsApp batch, the daily rescore, the Instagram poller...). A
    thread's CPU clock only ticks while that thread is actually running, so a
    60-second wait on the LLM costs the ~0 CPU seconds it really does.

The dashboard shows the process total minus the entries as "Everything
else" — never hidden, never guessed.

COST OF MEASURING: one lock, a dict update and two clock reads per request
or job (microseconds). The process clock and memory are sampled at most
every _SAMPLE_EVERY_SECONDS, the file is written at most every
_PERSIST_EVERY_SECONDS and only when something changed. No thread of its
own, no database. Kept for 48 hours (see usage_feed).

The file lives under DATA_DIR, OUTSIDE Backend/ (see usage_feed.DATA_DIR for why).
"""

from __future__ import annotations

import functools
import json
import threading
import time
from typing import Any, Callable, Dict, Optional, TypeVar

from Middleware import step_logger
from Service.BackendUsageService import usage_feed

_USAGE_PATH = usage_feed.DATA_DIR / "BackendUsage" / "cpu_usage_hourly.json"

KIND_REQUEST = "API request"
KIND_JOB = "Background job"
KIND_STARTUP = "Startup"
# The per-hour process total travels in the same feed under this label.
PROCESS_LABEL = "__process__"

_SAMPLE_EVERY_SECONDS = 5.0
_PERSIST_EVERY_SECONDS = 300.0
_PRUNE_EVERY_SECONDS = 600.0
# Labels are bounded by construction (route templates, fixed job names), so
# this is only a backstop against a bug growing the table without limit.
_MAX_BUCKETS = 12_000
_OVERFLOW_LABEL = "Other operations (label limit reached)"

F = TypeVar("F", bound=Callable[..., Any])

_lock = threading.Lock()
# "<ist hour>|<label>" -> {"hour", "label", "kind", "area", "count", "cpu", "wall", "max_cpu", "v"}
_buckets: Dict[str, Dict[str, Any]] = {}
# ist hour -> {"cpu", "rss_byte_seconds", "seconds", "rss_peak", "v"}
_process: Dict[int, Dict[str, Any]] = {}
_epoch = usage_feed.new_epoch()
_version = 0
_dirty = False
_warned = False
_started_at = time.time()
_last_persist = _started_at
_last_prune = 0.0
# process_time() counts from process start, so the first sample carries
# everything the process did before this module was imported (Python start,
# imports) — which Railway bills just the same.
_last_sample_wall = _started_at
_last_sample_cpu = 0.0
_local = threading.local()


def _warn_once(message: str) -> None:
    global _warned
    if not _warned:
        _warned = True
        step_logger.warn(f"{message} (vCPU tracking keeps going where it can; the app is unaffected.)")


# ------------------------------------------------------------------ write path


def _bucket_for_locked(hour: int, label: str, kind: str, area: str) -> Dict[str, Any]:
    key = f"{hour}|{label}"
    bucket = _buckets.get(key)
    if bucket is None and len(_buckets) >= _MAX_BUCKETS:
        label, kind, area = _OVERFLOW_LABEL, "Mixed", "Other"
        key = f"{hour}|{label}"
        bucket = _buckets.get(key)
    if bucket is None:
        bucket = {
            "hour": hour,
            "label": label,
            "kind": kind,
            "area": area,
            "count": 0,
            "cpu": 0.0,
            "wall": 0.0,
            "max_cpu": 0.0,
            "v": 0,
        }
        _buckets[key] = bucket
    return bucket


def record(label: str, kind: str, area: str, cpu_seconds: float, wall_seconds: float) -> None:
    """Files one finished request/job into its IST-hour bucket. Never raises."""
    global _version, _dirty
    try:
        now = time.time()
        with _lock:
            bucket = _bucket_for_locked(usage_feed.ist_hour_start(now), label, kind, area)
            cpu_seconds = max(0.0, float(cpu_seconds))
            bucket["count"] += 1
            bucket["cpu"] += cpu_seconds
            bucket["wall"] += max(0.0, float(wall_seconds))
            if cpu_seconds > bucket["max_cpu"]:
                bucket["max_cpu"] = cpu_seconds
            _version += 1
            bucket["v"] = _version
            _dirty = True
            _sample_process_locked(now)
            pending = _housekeep_locked(now)
        if pending is not None:
            _write(pending)
    except Exception as exc:  # noqa: BLE001
        _warn_once(f"Could not record vCPU usage for {label!r}: {exc!r}")


def _sample_process_locked(now: float, force: bool = False) -> None:
    """Adds the process CPU used since the last sample to the IST hour(s) it
    fell in — split by time when the interval crosses an hour boundary — and
    weights the current memory by the same interval."""
    global _last_sample_wall, _last_sample_cpu, _version, _dirty
    if not force and now - _last_sample_wall < _SAMPLE_EVERY_SECONDS:
        return
    cpu = time.process_time()
    delta_cpu = max(0.0, cpu - _last_sample_cpu)
    start = max(_last_sample_wall, now - usage_feed.RETENTION_SECONDS)
    span = max(0.0, now - start)
    _last_sample_cpu = cpu
    _last_sample_wall = now
    if delta_cpu <= 0.0 and span <= 0.0:
        return
    memory = usage_feed.billable_memory()
    _version += 1
    _dirty = True
    cursor = start
    while True:
        hour = usage_feed.ist_hour_start(cursor)
        end = min(now, hour + 3600)
        overlap = max(0.0, end - cursor)
        bucket = _process.get(hour)
        if bucket is None:
            bucket = {"cpu": 0.0, "rss_byte_seconds": 0.0, "seconds": 0.0, "rss_peak": 0, "v": 0}
            _process[hour] = bucket
        bucket["cpu"] += delta_cpu * (overlap / span) if span > 0 else delta_cpu
        if memory is not None and overlap > 0:
            bucket["rss_byte_seconds"] += memory * overlap
            bucket["seconds"] += overlap
            if memory > bucket["rss_peak"]:
                bucket["rss_peak"] = memory
        bucket["v"] = _version
        if end >= now:
            break
        cursor = end


def _housekeep_locked(now: float) -> Optional[str]:
    """Prunes past 48 h, and returns the file text when a (throttled) save is
    due — the caller writes it AFTER releasing the lock."""
    global _last_prune, _last_persist, _dirty
    if now - _last_prune >= _PRUNE_EVERY_SECONDS:
        _last_prune = now
        floor = usage_feed.retention_floor(now)
        for key in [key for key, bucket in _buckets.items() if bucket["hour"] < floor]:
            del _buckets[key]
        for hour in [hour for hour in _process if hour < floor]:
            del _process[hour]
    if _dirty and now - _last_persist >= _PERSIST_EVERY_SECONDS:
        _last_persist = now
        _dirty = False
        return _serialize_locked()
    return None


def _serialize_locked() -> str:
    return json.dumps(
        {
            "epoch": _epoch,
            "buckets": [{key: value for key, value in bucket.items() if key != "v"} for bucket in _buckets.values()],
            "process": [
                {"hour": hour, **{key: value for key, value in bucket.items() if key != "v"}}
                for hour, bucket in _process.items()
            ],
        },
        separators=(",", ":"),
    )


def _write(text: str) -> None:
    try:
        usage_feed.atomic_write(_USAGE_PATH, text)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Could not write the vCPU usage file ({_USAGE_PATH}): {exc!r}")


# ------------------------------------------------------------ job measurement


def enter_scope() -> bool:
    """Marks this thread as already inside a measured scope. Returns True for
    the outermost one — only that one records, so a tracked job called from
    inside a request (or another tracked job) is never counted twice."""
    depth = getattr(_local, "depth", 0)
    _local.depth = depth + 1
    return depth == 0


def exit_scope() -> None:
    _local.depth = max(0, getattr(_local, "depth", 1) - 1)


def tracked(label: str, area: str, kind: str = KIND_JOB) -> Callable[[F], F]:
    """Decorator for a background job's entry point: records the CPU its own
    thread used for the call. Changes nothing about the call itself — same
    arguments, same return value, same exceptions."""

    def decorate(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            outermost = enter_scope()
            started_cpu = time.thread_time() if outermost else 0.0
            started_wall = time.perf_counter() if outermost else 0.0
            try:
                return func(*args, **kwargs)
            finally:
                exit_scope()
                if outermost:
                    record(label, kind, area, time.thread_time() - started_cpu, time.perf_counter() - started_wall)

        return wrapper  # type: ignore[return-value]

    return decorate


def record_startup() -> None:
    """Called once from main.py's lifespan when start-up work is done: the
    whole process's CPU so far (interpreter start, imports, database init,
    loading saved state) is one entry. No request or tracked job can have run
    before it, so nothing inside it is counted twice."""
    record(
        "Server start-up — imports, database init, loading saved state",
        KIND_STARTUP,
        "Startup",
        time.process_time(),
        time.time() - _started_at,
    )


# ------------------------------------------------------------------ persistence


def load_from_disk() -> None:
    """Called once at startup (main.py). Merges what the last run saved, so a
    restart continues the same hours under the same epoch. Never raises."""
    global _epoch, _version
    try:
        if not _USAGE_PATH.exists():
            step_logger.info(f"vCPU usage history: none saved yet ({_USAGE_PATH}).")
            return
        raw = json.loads(_USAGE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"The vCPU usage file ({_USAGE_PATH}) could not be read: {exc!r}. Starting empty.")
        return
    if not isinstance(raw, dict):
        return
    floor = usage_feed.retention_floor(time.time())
    loaded = 0
    with _lock:
        epoch = raw.get("epoch")
        if isinstance(epoch, str) and epoch:
            _epoch = epoch
        _version += 1
        for row in raw.get("buckets") or []:
            try:
                hour = int(row["hour"])
                label = str(row["label"])
            except (KeyError, TypeError, ValueError):
                continue
            if hour < floor:
                continue
            bucket = _bucket_for_locked(hour, label, str(row.get("kind") or KIND_JOB), str(row.get("area") or "Other"))
            bucket["count"] += int(row.get("count") or 0)
            bucket["cpu"] += float(row.get("cpu") or 0.0)
            bucket["wall"] += float(row.get("wall") or 0.0)
            bucket["max_cpu"] = max(bucket["max_cpu"], float(row.get("max_cpu") or 0.0))
            bucket["v"] = _version
            loaded += 1
        for row in raw.get("process") or []:
            try:
                hour = int(row["hour"])
            except (KeyError, TypeError, ValueError):
                continue
            if hour < floor:
                continue
            bucket = _process.setdefault(
                hour, {"cpu": 0.0, "rss_byte_seconds": 0.0, "seconds": 0.0, "rss_peak": 0, "v": 0}
            )
            bucket["cpu"] += float(row.get("cpu") or 0.0)
            bucket["rss_byte_seconds"] += float(row.get("rss_byte_seconds") or 0.0)
            bucket["seconds"] += float(row.get("seconds") or 0.0)
            bucket["rss_peak"] = max(int(bucket["rss_peak"]), int(row.get("rss_peak") or 0))
            bucket["v"] = _version
    step_logger.info(f"vCPU usage history loaded: {loaded} hourly entr{'y' if loaded == 1 else 'ies'} ({_USAGE_PATH}).")


def reset() -> None:
    """Clears the 48-hour vCPU view — the Backend page's vCPU-tab Clear
    button. A new epoch (same mechanism as a lost file after a redeploy —
    see the module docstring) means any stale entry a browser still has
    cached is recognised as belonging to a bygone measurement instead of
    being added to what comes after. Purely local record-keeping: Railway's
    own billing is unaffected."""
    global _epoch, _version, _dirty
    with _lock:
        _buckets.clear()
        _process.clear()
        _epoch = usage_feed.new_epoch()
        _version += 1
        _dirty = True
        text = _serialize_locked()
    _write(text)
    step_logger.info("vCPU usage view cleared.")


def flush() -> None:
    """Writes whatever the throttle is still holding — called on shutdown so a
    clean restart loses nothing."""
    global _dirty, _last_persist
    try:
        with _lock:
            _sample_process_locked(time.time(), force=True)
            if not _dirty:
                return
            _dirty = False
            _last_persist = time.time()
            text = _serialize_locked()
        _write(text)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Could not flush the vCPU usage file: {exc!r}")


# ------------------------------------------------------------------ read path


def get_changes(cursor: Optional[str]) -> Dict[str, Any]:
    """Every hourly entry that changed after `cursor` (all of them for a new
    or other-process cursor), plus the per-hour process totals. Memory only."""
    since = usage_feed.parse_cursor(cursor)
    now = time.time()
    with _lock:
        _sample_process_locked(now, force=True)
        pending = _housekeep_locked(now)
        epoch = _epoch
        items = [
            {
                "k": f"{epoch}|{bucket['hour']}|{bucket['label']}",
                "hour": bucket["hour"],
                "label": bucket["label"],
                "kind": bucket["kind"],
                "area": bucket["area"],
                "count": bucket["count"],
                "cpu": round(bucket["cpu"], 6),
                "wall": round(bucket["wall"], 6),
                "max_cpu": round(bucket["max_cpu"], 6),
            }
            for bucket in _buckets.values()
            if bucket["v"] > since
        ]
        items.extend(
            {
                "k": f"{epoch}|{hour}|{PROCESS_LABEL}",
                "hour": hour,
                "label": PROCESS_LABEL,
                "cpu": round(bucket["cpu"], 6),
                "rss_byte_seconds": round(bucket["rss_byte_seconds"], 1),
                "seconds": round(bucket["seconds"], 3),
                "rss_peak": int(bucket["rss_peak"]),
            }
            for hour, bucket in _process.items()
            if bucket["v"] > since
        )
        position = _version
    if pending is not None:
        _write(pending)
    return {
        "cursor": usage_feed.make_cursor(position),
        "items": items,
        "python_threads": threading.active_count(),
        "uptime_seconds": round(now - _started_at),
    }
