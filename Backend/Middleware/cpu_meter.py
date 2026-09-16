"""Per-request vCPU measurement for the Dashboard's Backend tab.

A FastAPI request spends CPU in two places, so there are two pieces:

  1. The EVENT LOOP thread — routing, the middleware stack (CORS, GZip, the
     public rate limit), async dependencies, and rendering/compressing the
     response. CpuMeterMiddleware is the OUTERMOST middleware, so it sees all
     of it. That one thread is shared by every request in flight, so its CPU
     is settled like a ledger: each time a request starts or finishes, the
     loop CPU used since the previous settle is divided among the requests
     that were in flight during it. Nothing is counted twice, and loop time
     spent while NO request was running (idle, background asyncio work) is
     charged to no request — it stays in the tab's "Everything else".

  2. A WORKER thread — every `def` endpoint, every `def` dependency (the
     login check) and the response-model validation of a `def` endpoint run
     in Starlette's thread pool. install_threadpool_meter() wraps the
     `run_in_threadpool` name that fastapi.routing and
     fastapi.dependencies.utils call at request time, so that work runs
     inside a thin shim reading the worker thread's own CPU clock before and
     after. Same pool, same thread, same arguments, return value and
     exceptions — the shim only measures. If a future FastAPI stops exposing
     that name the install is skipped, and requests are then measured on the
     event loop only (still correct, just less complete).

Per request that is a few clock reads, a set add/remove and one
cpu_usage_service.record() call — microseconds.
"""

from __future__ import annotations

import contextvars
import threading
import time
from typing import Any, Callable, Optional, Set, Tuple

from Service.BackendUsageService import cpu_usage_service


class _Ticket:
    """One request's CPU, accumulated from both threads."""

    __slots__ = ("loop", "worker")

    def __init__(self) -> None:
        self.loop = 0.0
        self.worker = 0.0


_current: contextvars.ContextVar[Optional[_Ticket]] = contextvars.ContextVar("cpu_meter_ticket", default=None)

# Touched only on the event loop thread, so no lock is needed.
_inflight: Set[_Ticket] = set()
_loop_thread: Optional[int] = None
_loop_last: Optional[float] = None


def _settle() -> None:
    global _loop_last
    now = time.thread_time()
    if _loop_last is not None and _inflight:
        share = (now - _loop_last) / len(_inflight)
        if share > 0:
            for ticket in _inflight:
                ticket.loop += share
    _loop_last = now


def _enter(ticket: _Ticket) -> bool:
    global _loop_thread
    ident = threading.get_ident()
    if _loop_thread is None:
        _loop_thread = ident
    if ident != _loop_thread:
        # Never expected (uvicorn runs one event loop), but a second loop's
        # thread clock would corrupt this one's ledger — measure worker CPU only.
        return False
    _settle()
    _inflight.add(ticket)
    return True


def _leave(ticket: _Ticket) -> None:
    _settle()
    _inflight.discard(ticket)


def _label(scope: dict) -> Tuple[str, str]:
    """(label, area) for one request — the ROUTE TEMPLATE, never the raw URL,
    so ids in paths can't turn one endpoint into thousands of rows, and a bot
    probing random URLs on a public host lands in a single row."""
    method = scope.get("method") or "GET"
    if method == "OPTIONS":
        return "OPTIONS (CORS preflight)", "CORS"
    path = getattr(scope.get("route"), "path", None)
    if not isinstance(path, str) or not path:
        return "Unmatched URL (404)", "Other"
    segments = [segment for segment in path.split("/") if segment]
    if segments and segments[0] == "api":
        segments = segments[1:]
    area = segments[0] if segments else "root"
    return f"{method} {path}", area


class CpuMeterMiddleware:
    """Pure ASGI (no BaseHTTPMiddleware task hop). Only `http` scopes are
    measured; lifespan and anything else pass straight through."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        ticket = _Ticket()
        token = _current.set(ticket)
        on_loop = False
        try:
            on_loop = _enter(ticket)
        except Exception:  # noqa: BLE001 - measuring must never break a request
            on_loop = False
        started_wall = time.perf_counter()
        try:
            await self.app(scope, receive, send)
        finally:
            try:
                if on_loop:
                    _leave(ticket)
                label, area = _label(scope)
                cpu_usage_service.record(
                    label,
                    cpu_usage_service.KIND_REQUEST,
                    area,
                    ticket.loop + ticket.worker,
                    time.perf_counter() - started_wall,
                )
            except Exception:  # noqa: BLE001
                pass
            _current.reset(token)


def _metered(original: Callable[..., Any]) -> Callable[..., Any]:
    async def run_in_threadpool(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        ticket = _current.get()
        if ticket is None:
            return await original(func, *args, **kwargs)

        def measured() -> Any:
            # Also marks this worker thread as "inside a measured scope", so a
            # @tracked job called from within the request isn't counted twice.
            cpu_usage_service.enter_scope()
            started = time.thread_time()
            try:
                return func(*args, **kwargs)
            finally:
                ticket.worker += time.thread_time() - started
                cpu_usage_service.exit_scope()

        return await original(measured)

    run_in_threadpool._cpu_metered = True  # type: ignore[attr-defined]
    return run_in_threadpool


def install_threadpool_meter() -> None:
    """See the module docstring, point 2. Idempotent; never raises."""
    try:
        import fastapi.dependencies.utils as dependency_utils
        import fastapi.routing as routing
    except Exception:  # noqa: BLE001
        return
    for module in (routing, dependency_utils):
        original = getattr(module, "run_in_threadpool", None)
        if original is None or getattr(original, "_cpu_metered", False):
            continue
        setattr(module, "run_in_threadpool", _metered(original))
