"""Per-account failed-attempt limit. Unlike the per-IP middleware limit, this
can't be sidestepped by rotating addresses or X-Forwarded-For headers."""

from __future__ import annotations

import threading
import time
from typing import Dict, List

_WINDOW_SECONDS = 15 * 60
_MAX_FAILURES = 10
_MAX_TRACKED = 10_000

_lock = threading.Lock()
_failures: Dict[str, List[float]] = {}


def retry_after_seconds(key: str) -> int:
    now = time.monotonic()
    with _lock:
        recent = [t for t in _failures.get(key, []) if now - t < _WINDOW_SECONDS]
        if recent:
            _failures[key] = recent
        else:
            _failures.pop(key, None)
        if len(recent) < _MAX_FAILURES:
            return 0
        return int(_WINDOW_SECONDS - (now - recent[0])) + 1


def record_failure(key: str) -> None:
    with _lock:
        if key not in _failures and len(_failures) >= _MAX_TRACKED:
            del _failures[min(_failures, key=lambda k: _failures[k][-1])]
        attempts = _failures.setdefault(key, [])
        attempts.append(time.monotonic())
        del attempts[:-_MAX_FAILURES]


def clear(key: str) -> None:
    with _lock:
        _failures.pop(key, None)
