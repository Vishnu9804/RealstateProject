"""Token-usage analysis for the three LLM call sites in this app — read by
the Dashboard's "LLM Cost" tab. Mirrors the established pattern in
Service/WhatsAppDataFetchingService/area_knowledge_service.py: an
in-memory, lock-guarded snapshot, persisted as a plain JSON file OUTSIDE
Backend/ so uvicorn's --reload (which watches every *.py under Backend/)
never restarts the server — and drops every live WhatsApp connection —
over an ordinary usage write. See that module's own docstring for the full
reasoning; it applies identically here.

THE THREE SITES (fixed — pipeline STAGES, not models):
  - "property"    Agent/WhatsAppDataFetchingAgent/property_structurer.py
  - "requirement" Agent/WhatsAppDataFetchingAgent/glm_client.py, used by
                   Agent/BrokerRequirementAgent/requirement_structurer.py
  - "intent"      Agent/WhatsAppInquiryHandlingAgent/inquiry_classifier.py

Each site tracks its OWN breakdown by MODEL NAME — whatever
Config/settings.py's zai_model/zai_inquiry_model actually was at call time —
not a hardcoded list. Property and Requirement currently share one model
(ZAI_MODEL) and Intent uses its own (ZAI_INQUIRY_MODEL), but nothing here
assumes either fact: pointing a site at a different model tomorrow simply
starts a new row under that site the next time it's called, with no code
change here. All three sites now call the same Z.ai account (previously
Intent called Gemini, on a separate account), so all three also share
Agent/WhatsAppDataFetchingAgent/glm_gate.py's process-wide concurrency gate.

A site's own totals (calls / input / output / total tokens, and the three
per-call averages) are NEVER stored as a separate counter — get_overview()
always computes them as the SUM of that site's per-model rows. That is what
guarantees the dashboard's top-of-tab totals can never drift out of sync
with the per-model rows shown underneath: there is only one number being
computed, not two kept in step by hand.

HOUR BY HOUR: every call is also filed into an IST-hour bucket (per site and
model), kept for 48 hours in a second small file, llm_usage_hourly.json. The
dashboard pulls those buckets as a cursor-based delta (see
Service/BackendUsageService/usage_feed.py) and shows one card per hour. The
all-time counters above are untouched by this and keep counting exactly as
before.

WHAT COUNTS AS "ONE CALL": one successful completion — a request that
actually returned usable content. A request that failed and was retried
(see each call site's own retry policy) is not counted at all, since there
is nothing to attribute tokens to; if a provider succeeds but doesn't
report usage for some reason, the call still increments the calls counter
but contributes 0 to the token counts. This is an engineering-facing
usage/cost TREND view, not audit-grade billing reconciliation (which would
also need every billed-but-failed attempt).

SAFETY: called from whichever thread each call site runs on (the property
and requirement pipelines flush on their own background thread, same as
area_knowledge_service) while the API reads it from FastAPI's thread pool,
so all shared state is guarded by one re-entrant lock. observe_call() must
never raise into a real pipeline call over a stats bug — every call site
wraps its own call to it in a try/except as well, exactly like
area_knowledge_service.observe_properties().
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from Middleware import step_logger
from Service.BackendUsageService import usage_feed

# Backend/Service/LLMUsageService/this_file.py
#   parents[0] = .../Service/LLMUsageService
#   parents[1] = .../Service
#   parents[2] = .../Backend        <- uvicorn's --reload watch root
#   parents[3] = .../<project root> <- outside it, on purpose (see docstring)
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_PROJECT_ROOT = _BACKEND_DIR.parent
_USAGE_DIR = _PROJECT_ROOT / "LLMUsage"
_USAGE_PATH = _USAGE_DIR / "llm_usage_stats.json"
_HOURLY_PATH = _USAGE_DIR / "llm_usage_hourly.json"

# The only three tabs the dashboard shows. A call site passing anything
# else is a bug on that call site's part (see observe_call), not something
# this module tries to accommodate.
SITES = ("property", "requirement", "intent")

_lock = threading.RLock()

# site -> model -> {"calls": int, "input_tokens": int, "output_tokens": int}
_usage: Dict[str, Dict[str, Dict[str, int]]] = {site: {} for site in SITES}
_loaded = False

# "<ist hour>|<site>|<model>" -> {"hour", "site", "model", "calls",
# "input_tokens", "output_tokens", "v"} — the last 48 hours only.
_hourly: Dict[str, Dict[str, Any]] = {}
_hourly_epoch = usage_feed.new_epoch()
_hourly_version = 0
_hourly_loaded = False


def _atomic_write(path: Path, text: str) -> None:
    """Write-then-rename, so a reader (or a crash) can never see a half
    written usage file — it either has the old contents or the new ones."""
    _USAGE_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def _persist() -> None:
    try:
        _atomic_write(_USAGE_PATH, json.dumps(_usage, indent=2, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        # In-memory counters are still correct and still measured; only the
        # save failed. Never worth costing the pipeline call that triggered it.
        step_logger.error(f"Could not write the LLM usage stats file ({_USAGE_PATH}): {exc!r}")


def _persist_hourly() -> None:
    try:
        rows = [{key: value for key, value in row.items() if key != "v"} for row in _hourly.values()]
        _atomic_write(
            _HOURLY_PATH,
            json.dumps({"epoch": _hourly_epoch, "rows": rows}, ensure_ascii=False, separators=(",", ":")),
        )
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Could not write the hourly LLM usage file ({_HOURLY_PATH}): {exc!r}")


def _hourly_bucket_locked(hour: int, site: str, model: str) -> Dict[str, Any]:
    key = f"{hour}|{site}|{model}"
    bucket = _hourly.get(key)
    if bucket is None:
        bucket = {"hour": hour, "site": site, "model": model, "calls": 0, "input_tokens": 0, "output_tokens": 0, "v": 0}
        _hourly[key] = bucket
    return bucket


def _load_hourly_locked() -> None:
    """Once per process: the last run's hourly buckets, same epoch, so the
    dashboard keeps adding to the same hours after a restart."""
    global _hourly_epoch, _hourly_version, _hourly_loaded
    if _hourly_loaded:
        return
    _hourly_loaded = True
    if not _HOURLY_PATH.exists():
        return
    try:
        raw = json.loads(_HOURLY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"The hourly LLM usage file ({_HOURLY_PATH}) could not be read: {exc!r}. Starting empty.")
        return
    if not isinstance(raw, dict):
        return
    epoch = raw.get("epoch")
    if isinstance(epoch, str) and epoch:
        _hourly_epoch = epoch
    floor = usage_feed.retention_floor(time.time())
    _hourly_version += 1
    for row in raw.get("rows") or []:
        try:
            hour = int(row["hour"])
            site = str(row["site"])
            model = str(row["model"])
        except (KeyError, TypeError, ValueError):
            continue
        if hour < floor:
            continue
        bucket = _hourly_bucket_locked(hour, site, model)
        bucket["calls"] += max(0, int(row.get("calls") or 0))
        bucket["input_tokens"] += max(0, int(row.get("input_tokens") or 0))
        bucket["output_tokens"] += max(0, int(row.get("output_tokens") or 0))
        bucket["v"] = _hourly_version


def load_from_disk() -> None:
    """Called once at startup (Backend/main.py), same lifecycle as
    area_knowledge_service.load_from_disk(). Never raises — a usage file
    that fails to read starts this process from empty counters rather than
    stopping the server; the file itself is left untouched either way, so a
    later fix to it (or a later successful read) loses nothing."""
    global _loaded
    with _lock:
        for site in SITES:
            _usage.setdefault(site, {})
        if _USAGE_PATH.exists():
            try:
                raw = json.loads(_USAGE_PATH.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                step_logger.error(
                    f"The LLM usage stats file ({_USAGE_PATH}) could not be read: {exc!r}. "
                    "Starting this process's counters from empty — nothing on disk is touched "
                    "until the next successful observe_call() write."
                )
                raw = None
            if isinstance(raw, dict):
                for site, models in raw.items():
                    if not isinstance(models, dict):
                        continue
                    bucket = _usage.setdefault(site, {})
                    for model, counters in models.items():
                        if not isinstance(counters, dict):
                            continue
                        bucket[model] = {
                            "calls": max(0, int(counters.get("calls", 0) or 0)),
                            "input_tokens": max(0, int(counters.get("input_tokens", 0) or 0)),
                            "output_tokens": max(0, int(counters.get("output_tokens", 0) or 0)),
                        }
        try:
            _load_hourly_locked()
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"Could not load the hourly LLM usage (the all-time counters are unaffected): {exc!r}")
        _loaded = True
        total_calls = sum(counters["calls"] for models in _usage.values() for counters in models.values())
        step_logger.info(f"LLM usage stats loaded: {total_calls} call(s) recorded so far ({_USAGE_PATH}).")


def _observe_hourly_locked(site: str, model: str, input_tokens: int, output_tokens: int) -> None:
    global _hourly_version
    now = time.time()
    floor = usage_feed.retention_floor(now)
    for key in [key for key, row in _hourly.items() if row["hour"] < floor]:
        del _hourly[key]
    bucket = _hourly_bucket_locked(usage_feed.ist_hour_start(now), site, model)
    bucket["calls"] += 1
    bucket["input_tokens"] += input_tokens
    bucket["output_tokens"] += output_tokens
    _hourly_version += 1
    bucket["v"] = _hourly_version
    _persist_hourly()


def observe_call(site: str, model: str, input_tokens: int, output_tokens: int) -> None:
    """THE ONLY WRITE PATH. Called once per successful LLM completion, right
    where each call site consumes its response — see the module docstring
    for exactly where that is for each of the three sites. Never raises:
    every call site also wraps its own call to this in a try/except, but
    this function guards itself too so a stats bug can never propagate."""
    try:
        model = (model or "unknown").strip() or "unknown"
        input_tokens = max(0, int(input_tokens or 0))
        output_tokens = max(0, int(output_tokens or 0))
        if site not in SITES:
            # Every call site passes a literal from SITES — this should be
            # unreachable in practice, but a typo on a call site must never
            # crash a real property/requirement/inquiry call over this.
            step_logger.warn(f"LLM usage observed for an unrecognised site {site!r} — recording it anyway.")

        with _lock:
            if not _loaded:
                load_from_disk()
            bucket = _usage.setdefault(site, {})
            counters = bucket.setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
            counters["calls"] += 1
            counters["input_tokens"] += input_tokens
            counters["output_tokens"] += output_tokens
            call_number = counters["calls"]
            _persist()
            # After the all-time counters are safely updated and saved: the
            # hourly view is additive and must never cost them anything.
            try:
                _observe_hourly_locked(site, model, input_tokens, output_tokens)
            except Exception as exc:  # noqa: BLE001
                step_logger.error(f"Could not record hourly LLM usage ({site}/{model}): {exc!r}")

        step_logger.info(
            f"LLM usage: {site}/{model} call #{call_number} (+{input_tokens} in / +{output_tokens} out tokens)."
        )
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"Could not record LLM usage ({site}/{model}): {exc!r}")


def _metrics(calls: int, input_tokens: int, output_tokens: int) -> Dict[str, Any]:
    total_tokens = input_tokens + output_tokens
    return {
        "calls": calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "avg_input_tokens_per_call": round(input_tokens / calls, 1) if calls else 0.0,
        "avg_output_tokens_per_call": round(output_tokens / calls, 1) if calls else 0.0,
        "avg_total_tokens_per_call": round(total_tokens / calls, 1) if calls else 0.0,
    }


def get_overview() -> Dict[str, Any]:
    """Everything the LLM Cost tab shows, in one snapshot: for each of the
    three sites, its totals (always the sum of the rows below it, computed
    right here — never a separately-tracked counter) and its per-model
    breakdown, most-called model first."""
    with _lock:
        sites: Dict[str, Any] = {}
        for site in SITES:
            models_raw = _usage.get(site, {})
            model_rows = []
            site_calls = site_input = site_output = 0
            for model, counters in sorted(
                models_raw.items(), key=lambda item: (-int(item[1].get("calls", 0)), item[0])
            ):
                calls = int(counters.get("calls", 0))
                input_tokens = int(counters.get("input_tokens", 0))
                output_tokens = int(counters.get("output_tokens", 0))
                site_calls += calls
                site_input += input_tokens
                site_output += output_tokens
                model_rows.append({"model": model, **_metrics(calls, input_tokens, output_tokens)})
            sites[site] = {
                "totals": _metrics(site_calls, site_input, site_output),
                "models": model_rows,
            }
        return {"sites": sites}


def reset_hourly_for_site(site: str) -> None:
    """Clears the 48-hour view for ONE site only — the LLM Cost tab's
    per-tab Clear button. The all-time counters (get_overview) are a
    separate, permanent record of real cost history and are deliberately
    left untouched, exactly like area_knowledge_service.reset_stats keeps
    the learned place strings: this only restarts what "the last 48 hours"
    measures from, it never throws away a real call's cost.

    Rows are deleted outright rather than zeroed in place, so a cleared
    hour actually disappears (back to the "No calls" empty state) instead
    of lingering as a wall of 0-call cards."""
    with _lock:
        if not _loaded:
            load_from_disk()
        if site not in SITES:
            raise ValueError(f"Unknown site {site!r}")
        for key in [key for key, row in _hourly.items() if row["site"] == site]:
            del _hourly[key]
        _persist_hourly()
    step_logger.info(f"LLM hourly usage view cleared for site {site!r} — the all-time counters were kept.")


def get_hourly_changes(cursor: Optional[str]) -> Dict[str, Any]:
    """The hourly buckets changed after `cursor` (all of them for a new or
    other-process cursor), last 48 hours. Memory only."""
    since = usage_feed.parse_cursor(cursor)
    with _lock:
        if not _loaded:
            load_from_disk()
        floor = usage_feed.retention_floor(time.time())
        items = [
            {
                "k": f"{_hourly_epoch}|{row['hour']}|{row['site']}|{row['model']}",
                "hour": row["hour"],
                "site": row["site"],
                "model": row["model"],
                "calls": row["calls"],
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
            }
            for row in _hourly.values()
            if row["v"] > since and row["hour"] >= floor
        ]
        position = _hourly_version
    return {"cursor": usage_feed.make_cursor(position), "items": items}
