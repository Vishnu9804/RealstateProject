"""What is sitting in the backend's RAM right now — for the Dashboard's
Backend tab (RAM capsule).

Railway bills a container for the memory it holds, every second it holds it,
so the useful question is "what is that memory made of?". This module
answers it on demand:

  - the OS's own figure for the whole process (RSS) and, inside a Linux
    container, the container's working set — what the platform itself sees
    and the closer of the two to what Railway charges for;
  - every in-process cache this app keeps, by name, with how many things are
    in it and how much memory they take (e.g. "512 properties = 6.2 MB, of
    which 384-dimension vectors are 5.1 MB");
  - the embedding model's weights, counted exactly from its tensors;
  - and, as the remainder, everything a Python process cannot itemise about
    itself: the interpreter, PyTorch/NumPy's native libraries, the WhatsApp
    Go runtime, allocator overhead. Nothing is hidden — the rows plus the
    remainder add up to the total.

HOW SIZES ARE MEASURED: sys.getsizeof over the real objects, following
references (dicts, lists, pydantic models, dataclasses), each object counted
once. Small caches are measured item by item; large ones on an evenly spaced
sample of _SAMPLE_SIZE items, scaled up — the tab marks those with "≈". Only
modules that are ALREADY imported are inspected: measuring must never be the
thing that loads PyTorch into memory.

COST: built only when the RAM capsule asks for it, cached for
_CACHE_SECONDS so several open dashboards share one measurement, and bounded
(_OBJECT_BUDGET objects per cache). No thread, no database, no disk.
"""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from datetime import time as clock_time
from types import BuiltinFunctionType, FunctionType, MethodType, ModuleType
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel

from Service.BackendUsageService import usage_feed

_SAMPLE_SIZE = 48
_OBJECT_BUDGET = 80_000
_CACHE_SECONDS = 20.0

_ATOMIC = (str, bytes, bytearray, int, float, complex, bool, date, clock_time, timedelta)
_NOT_DATA = (type, ModuleType, FunctionType, BuiltinFunctionType, MethodType)

_WA = "Service.WhatsAppDataFetchingService."
_FALLBACK_NOTE = "Only used when DATABASE_URL is unset."

# (group id, group label, specs). A spec names a module-level container by
# module + attribute path; "measure" picks how it is sized.
_GROUPS: Tuple[Tuple[str, str, Tuple[Dict[str, Any], ...]], ...] = (
    (
        "properties",
        "Property data",
        (
            {
                "id": "property_snapshot",
                "label": "Property snapshot",
                "module": _WA + "property_snapshot",
                "attrs": ("_entries",),
                "unit": "properties",
                "measure": "snapshot",
                "detail": "Every property the app works from, photos excluded — what the list pages, client matching "
                "and the Instagram poller read instead of querying Neon. Loaded once, then kept in step with every write.",
            },
            {
                "id": "soldout_snapshot",
                "label": "Sold-out snapshot",
                "module": _WA + "soldout_property_store",
                "attrs": ("_entries",),
                "unit": "sold-out properties",
                "detail": "The Sold out tab's rows, held so that tab and its change marker never query Neon.",
            },
            {
                "id": "builder_projects",
                "label": "Builder projects",
                "module": "Service.BuilderProjectService.builder_project_store",
                "attrs": ("_entries",),
                "unit": "projects",
                "detail": "The Builder Projects list plus each project's match vector, for the page and for matching.",
            },
            {
                "id": "property_fallback",
                "label": "Properties — in-memory store",
                "module": _WA + "property_vector_store",
                "attrs": ("_properties",),
                "unit": "properties",
                "measure": "property_list",
                "fallback": True,
                "detail": "The whole property store, photos included. " + _FALLBACK_NOTE,
            },
            {
                "id": "property_fingerprints",
                "label": "Property message fingerprints — in-memory",
                "module": _WA + "property_vector_store",
                "attrs": ("_message_fingerprints",),
                "unit": "fingerprints",
                "fallback": True,
                "detail": "The exact-duplicate check's lookup table. " + _FALLBACK_NOTE,
            },
        ),
    ),
    (
        "requirements",
        "Broker requirements",
        (
            {
                "id": "requirement_vectors",
                "label": "Requirement embedding cache",
                "module": "Service.BrokerRequirementService.requirement_matching_service",
                "attrs": ("_vector_cache",),
                "unit": "vectors",
                "detail": "Each scored requirement's 384-dim vector, keyed by the text it was built from, so opening "
                "its matches again never re-runs the embedding model. Capped at 500.",
            },
            {
                "id": "requirement_fallback",
                "label": "Requirements — in-memory store",
                "module": "Service.BrokerRequirementService.requirement_store",
                "attrs": ("_requirements",),
                "unit": "requirements",
                "fallback": True,
                "detail": _FALLBACK_NOTE,
            },
            {
                "id": "requirement_fingerprints",
                "label": "Requirement message fingerprints — in-memory",
                "module": "Service.BrokerRequirementService.requirement_store",
                "attrs": ("_message_fingerprints",),
                "unit": "fingerprints",
                "fallback": True,
                "detail": _FALLBACK_NOTE,
            },
            {
                "id": "requirement_matches",
                "label": "Requirement matches — in-memory store",
                "module": "Service.BrokerRequirementService.requirement_match_store",
                "attrs": ("_matches",),
                "unit": "requirements",
                "fallback": True,
                "detail": _FALLBACK_NOTE,
            },
        ),
    ),
    (
        "clients",
        "Clients, agents & accounts",
        (
            {
                "id": "users",
                "label": "Login accounts",
                "module": "Service.AuthManagementService.user_store",
                "attrs": ("_users",),
                "unit": "accounts",
                "detail": "Every login account, loaded once at startup so signing in and checking a token never "
                "query Neon.",
            },
            {
                "id": "known_clients",
                "label": "Known-client lookup cache",
                "module": "Service.WhatsAppInquiryHandlingService.known_client_cache",
                "attrs": ("_answers",),
                "unit": "phone numbers",
                "detail": "Recent \"is this number already a client?\" answers for the public site — each one saves a "
                "Neon lookup. Entries expire by themselves.",
            },
            {
                "id": "instagram_events",
                "label": "Instagram events already handled",
                "module": "Service.InstagramInquiryHandlingService.instagram_contact_store",
                "attrs": ("_processed_events", "_processed_cache"),
                "unit": "events",
                "detail": "Comment/DM ids already answered, so a webhook Meta re-delivers is recognised from "
                "memory instead of costing a Neon lookup.",
            },
            {
                "id": "instagram_media",
                "label": "Instagram reel ↔ media-id index",
                "module": "Service.InstagramInquiryHandlingService.instagram_reel_matcher",
                "attrs": ("_media_code", "_media_unresolvable"),
                "unit": "media",
                "detail": "Which Instagram post each media id is, so a reel's permalink is looked up once ever "
                "— and comments on posts that are not properties cost nothing at all.",
            },
            {
                "id": "client_fallback",
                "label": "Clients — in-memory store",
                "module": "Service.WhatsAppInquiryHandlingService.client_store",
                "attrs": ("_clients", "_matches_computed_at"),
                "unit": "clients",
                "fallback": True,
                "detail": _FALLBACK_NOTE,
            },
            {
                "id": "client_photos",
                "label": "Client photos — in-memory store",
                "module": "Service.WhatsAppInquiryHandlingService.client_store",
                "attrs": ("_client_photos",),
                "unit": "photos",
                "fallback": True,
                "detail": _FALLBACK_NOTE,
            },
            {
                "id": "client_scores",
                "label": "Client match scores — in-memory store",
                "module": "Service.ClientPropertyMatchingService.matching_service",
                "attrs": ("_score_cache", "_computed_at_cache"),
                "unit": "clients",
                "fallback": True,
                "detail": _FALLBACK_NOTE,
            },
            {
                "id": "agents",
                "label": "Agents, visits & assignments — in-memory store",
                "module": "Service.AgentManagementService.agent_store",
                "attrs": ("_agents", "_visits", "_assignments"),
                "unit": "agents",
                "fallback": True,
                "detail": _FALLBACK_NOTE,
            },
            {
                "id": "instagram_contacts",
                "label": "Instagram contacts — in-memory store",
                "module": "Service.InstagramInquiryHandlingService.instagram_contact_store",
                "attrs": ("_contacts",),
                "unit": "contacts",
                "fallback": True,
                "detail": _FALLBACK_NOTE,
            },
            {
                "id": "landing_leads",
                "label": "Landing-page leads — in-memory store",
                "module": "Service.LandingPageService.lead_store",
                "attrs": ("_leads",),
                "unit": "leads",
                "fallback": True,
                "detail": _FALLBACK_NOTE,
            },
        ),
    ),
    (
        "ai",
        "AI model & LLM helpers",
        (
            {
                "id": "embedding_model",
                "label": "Embedding model weights",
                "unit": "model",
                "measure": "embedding_model",
                "detail": "",
            },
            {
                "id": "area_recall",
                "label": "GLM area-knowledge (STEP A) cache",
                "module": "Agent.WhatsAppDataFetchingAgent.property_structurer",
                "attrs": ("_area_knowledge_cache",),
                "unit": "area lists",
                "detail": "The model's recall for the current selected areas, reused so each property batch doesn't "
                "pay output tokens to regenerate it.",
            },
            {
                "id": "area_kb",
                "label": "Surat area knowledge base",
                "module": _WA + "area_knowledge_service",
                "attrs": ("_area_places", "_place_owner", "_area_display"),
                "unit": "areas",
                "detail": "The learned place strings per area (Knowledge Base tab) and their reverse lookup.",
            },
            {
                "id": "area_kb_stats",
                "label": "Area knowledge base — analysis",
                "module": _WA + "area_knowledge_service",
                "attrs": ("_events", "_area_stats", "_stats"),
                "unit": "recent events",
                "detail": "The Knowledge Base tab's activity feed and counters.",
            },
        ),
    ),
    (
        "intake",
        "WhatsApp intake",
        (
            {
                "id": "captured",
                "label": "Recent captured messages",
                "module": _WA + "whatsapp_service",
                "attrs": ("_captured_messages",),
                "unit": "messages",
                "detail": "The last messages seen on monitored chats. Capped at 500.",
            },
            {
                "id": "qualified",
                "label": "Recent qualified messages",
                "module": _WA + "whatsapp_service",
                "attrs": ("_qualified_messages",),
                "unit": "messages",
                "detail": "The last messages that passed the property filter. Capped at 500.",
            },
            {
                "id": "property_buffer",
                "label": "Property batch waiting for the LLM",
                "module": _WA + "whatsapp_service",
                "attrs": ("_message_buffer._buffer",),
                "unit": "messages",
                "detail": "Messages collected towards the next property LLM call (10, or when the batch window ends).",
            },
            {
                "id": "requirement_buffer",
                "label": "Requirement batch waiting for the LLM",
                "module": _WA + "whatsapp_service",
                "attrs": ("_requirement_buffer._buffer",),
                "unit": "messages",
                "detail": "Messages collected towards the next requirement LLM call.",
            },
            {
                "id": "inquiry_captured",
                "label": "Recent client-inquiry messages",
                "module": "Service.WhatsAppInquiryHandlingService.whatsapp_inquiry_service",
                "attrs": ("_captured_messages",),
                "unit": "messages",
                "detail": "The last 1:1 inquiry messages. Capped at 500.",
            },
        ),
    ),
    (
        "guards",
        "Rate limits & throttles",
        (
            {
                "id": "rate_limit",
                "label": "Public rate-limit table",
                "module": "Middleware.public_rate_limit",
                "attrs": ("_hits", "_last_touched"),
                "unit": "address buckets",
                "detail": "Request times per address for the public form endpoints. Capped at 20,000 addresses.",
            },
            {
                "id": "otp_history",
                "label": "OTP send history",
                "module": "Service.WhatsAppInquiryHandlingService.otp_service",
                "attrs": ("_send_history",),
                "unit": "phone numbers",
                "detail": "Recent verification-code sends per number (the per-number send limit).",
            },
            {
                "id": "login_throttle",
                "label": "Login throttle",
                "module": "Service.AuthManagementService.login_throttle",
                "attrs": ("_failures",),
                "unit": "keys",
                "detail": "Recent failed sign-ins, for the lockout.",
            },
        ),
    ),
    (
        "dashboard",
        "This dashboard's own trackers",
        (
            {
                "id": "neon_windows",
                "label": "Neon DB tab — wake-up history",
                "module": "Service.NeonUsageService.neon_usage_service",
                "attrs": ("_windows",),
                "unit": "wake-ups",
                "detail": "Every Neon wake-up of the last 48 hours, with its operations.",
            },
            {
                "id": "llm_hourly",
                "label": "LLM Cost tab — hourly buckets",
                "module": "Service.LLMUsageService.llm_usage_service",
                "attrs": ("_hourly", "_usage"),
                "unit": "hour × model rows",
                "detail": "Tokens per IST hour and model for 48 hours, plus the all-time totals.",
            },
            {
                "id": "cpu_hourly",
                "label": "vCPU tab — hourly entries",
                "module": "Service.BackendUsageService.cpu_usage_service",
                "attrs": ("_buckets", "_process"),
                "unit": "hour × operation rows",
                "detail": "CPU per IST hour per endpoint/job for 48 hours.",
            },
            {
                "id": "message_index",
                "label": "Message to Model — index only",
                "module": "Service.LLMUsageService.message_model_service",
                "attrs": ("_offsets", "_seqs", "_times"),
                "unit": "logged messages",
                "detail": "Three numbers per logged message. The messages and their models live on disk "
                "(LLMUsage/message_model_log.jsonl), not in RAM.",
            },
        ),
    ),
)


class _BudgetSpent(Exception):
    pass


# ------------------------------------------------------------------- sizing


def _deep_size(root: Any, seen: set, budget: List[int]) -> int:
    """Bytes held by `root` and everything it references that `seen` hasn't
    already counted. Iterative (no recursion limit), and every container is
    copied with one C-level call before walking it, so a cache being written
    by another thread at the same moment can't break the walk."""
    total = 0
    stack = [root]
    while stack:
        obj = stack.pop()
        if obj is None or obj is True or obj is False:
            continue
        identity = id(obj)
        if identity in seen:
            continue
        seen.add(identity)
        if isinstance(obj, _NOT_DATA):
            continue
        if isinstance(obj, int) and -5 <= obj <= 256:
            continue  # CPython's shared small ints
        budget[0] -= 1
        if budget[0] < 0:
            raise _BudgetSpent
        try:
            total += sys.getsizeof(obj)
        except TypeError:
            continue
        if isinstance(obj, _ATOMIC):
            continue
        if isinstance(obj, dict):
            for key, value in list(obj.items()):
                stack.append(key)
                stack.append(value)
        elif isinstance(obj, (list, tuple, set, frozenset, deque)):
            stack.extend(list(obj))
        elif isinstance(obj, BaseModel):
            # Field names are shared by every instance, so only the values
            # (and the dict/set objects themselves) belong to this one.
            fields = obj.__dict__
            if id(fields) not in seen:
                seen.add(id(fields))
                total += sys.getsizeof(fields)
                stack.extend(list(fields.values()))
            fields_set = getattr(obj, "__pydantic_fields_set__", None)
            if fields_set is not None and id(fields_set) not in seen:
                seen.add(id(fields_set))
                total += sys.getsizeof(fields_set)
            extra = getattr(obj, "__pydantic_extra__", None)
            if extra:
                stack.append(extra)
        else:
            attributes = getattr(obj, "__dict__", None)
            if isinstance(attributes, dict) and id(attributes) not in seen:
                seen.add(id(attributes))
                total += sys.getsizeof(attributes)
                stack.extend(list(attributes.values()))
            for cls in type(obj).__mro__:
                slots = getattr(cls, "__slots__", ())
                if isinstance(slots, str):
                    slots = (slots,)
                for name in slots:
                    if name in ("__dict__", "__weakref__"):
                        continue
                    value = getattr(obj, name, None)
                    if value is not None:
                        stack.append(value)
    return total


def _sample(entries: List[Any]) -> List[Any]:
    count = len(entries)
    if count <= _SAMPLE_SIZE:
        return entries
    return [entries[index * count // _SAMPLE_SIZE] for index in range(_SAMPLE_SIZE)]


def _measure_container(container: Any) -> Tuple[int, int, bool]:
    """(item count, bytes, measured every item)."""
    if isinstance(container, dict):
        entries: List[Any] = list(container.items())
        is_mapping = True
    elif isinstance(container, (list, tuple, set, frozenset, deque)):
        entries = list(container)
        is_mapping = False
    else:
        seen: set = set()
        try:
            return 1, _deep_size(container, seen, [_OBJECT_BUDGET]), True
        except _BudgetSpent:
            return 1, 0, False
    count = len(entries)
    base = sys.getsizeof(container)
    if not count:
        return 0, base, True
    sample = _sample(entries)
    seen = {id(container)}
    budget = [_OBJECT_BUDGET]
    measured = 0
    done = 0
    try:
        for entry in sample:
            if is_mapping:
                measured += _deep_size(entry[0], seen, budget) + _deep_size(entry[1], seen, budget)
            else:
                measured += _deep_size(entry, seen, budget)
            done += 1
    except _BudgetSpent:
        pass
    if done == 0:
        return count, base, False
    return count, base + int(measured * count / done), done == count


def _measure_properties(
    container: Any, prop_of: Callable[[Any], Any]
) -> Tuple[int, int, bool, List[Dict[str, Any]]]:
    """_measure_container for a list of properties, plus where their bytes
    go — the vectors and the copied message text are the two things worth
    knowing about when the snapshot grows."""
    entries = list(container)
    count = len(entries)
    base = sys.getsizeof(container)
    if not count:
        return 0, base, True, []
    seen = {id(container)}
    budget = [_OBJECT_BUDGET]
    measured = vectors = texts = photos = 0
    done = 0
    try:
        for entry in _sample(entries):
            measured += _deep_size(entry, seen, budget)
            done += 1
            prop = prop_of(entry)
            embedding = getattr(prop, "embedding", None)
            if isinstance(embedding, list):
                vectors += sys.getsizeof(embedding) + sum(sys.getsizeof(value) for value in embedding)
            message_text = getattr(prop, "message_text", None)
            if isinstance(message_text, str):
                texts += sys.getsizeof(message_text)
            images = getattr(prop, "image_urls", None)
            if isinstance(images, list) and images:
                photos += sys.getsizeof(images) + sum(sys.getsizeof(image) for image in images if isinstance(image, str))
    except _BudgetSpent:
        pass
    if done == 0:
        return count, base, False, []
    scale = count / done
    total = base + int(measured * scale)
    breakdown = [
        {"label": label, "bytes": int(value * scale)}
        for label, value in (
            ("384-dim embedding vectors", vectors),
            ("Copied WhatsApp message text", texts),
            ("Photos (base64)", photos),
        )
        if value
    ]
    rest = total - sum(part["bytes"] for part in breakdown)
    breakdown.append({"label": "Fields, timestamps & object overhead", "bytes": max(0, rest)})
    return count, total, done == count, breakdown


def _measure_embedding_model() -> Dict[str, Any]:
    module = sys.modules.get(_WA + "embedding_service")
    name = getattr(module, "EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
    model = getattr(module, "_model", None) if module is not None else None
    if model is None:
        return {
            "loaded": False,
            "detail": f"{name} is not loaded in this process yet — it loads on the first property embedding or "
            "match scoring. Once it does, expect ~90 MB of weights here, plus the PyTorch runtime in "
            "\"runtime & libraries\".",
        }
    parameters = 0
    weight_bytes = 0
    dtype = ""
    for tensor in model.parameters():
        elements = int(tensor.numel())
        parameters += elements
        weight_bytes += elements * int(tensor.element_size())
        dtype = dtype or str(tensor.dtype).replace("torch.", "")
    for tensor in model.buffers():
        weight_bytes += int(tensor.numel()) * int(tensor.element_size())
    return {
        "loaded": True,
        "count": 1,
        "bytes": weight_bytes,
        "exact": True,
        "detail": f"{name} — {parameters / 1e6:.1f} M parameters ({dtype}), counted exactly from its tensors. "
        "Weights only: the PyTorch/transformers runtime that executes them is part of \"runtime & libraries\".",
    }


def _resolve(root: Any, path: str) -> Any:
    value = root
    for part in path.split("."):
        value = getattr(value, part, None)
        if value is None:
            return None
    return value


def _measure_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "id": spec["id"],
        "label": spec["label"],
        "detail": spec["detail"],
        "unit": spec["unit"],
        "count": 0,
        "bytes": 0,
        "exact": True,
        "loaded": False,
        "fallback_only": bool(spec.get("fallback")),
        "breakdown": [],
    }
    measure = spec.get("measure", "generic")
    try:
        if measure == "embedding_model":
            item.update(_measure_embedding_model())
            return item
        module = sys.modules.get(spec["module"])
        if module is None:
            item["detail"] += " (Not imported in this process.)"
            return item
        item["loaded"] = True
        containers = [_resolve(module, path) for path in spec["attrs"]]
        if containers[0] is None:
            return item
        breakdown: List[Dict[str, Any]] = []
        if measure == "snapshot":
            count, size, exact, breakdown = _measure_properties(containers[0], lambda entry: entry.prop)
            by_id = getattr(module, "_by_id", None)
            if by_id is not None:
                size += sys.getsizeof(by_id)  # its values are the entries already counted
        elif measure == "property_list":
            count, size, exact, breakdown = _measure_properties(containers[0], lambda prop: prop)
        else:
            count, size, exact = _measure_container(containers[0])
            for extra in containers[1:]:
                if extra is not None:
                    _extra_count, extra_size, extra_exact = _measure_container(extra)
                    size += extra_size
                    exact = exact and extra_exact
        item.update(count=count, bytes=size, exact=exact, breakdown=breakdown)
    except Exception as exc:  # noqa: BLE001 - one odd cache must not sink the whole snapshot
        item["detail"] += f" (Could not be measured: {type(exc).__name__}.)"
    return item


# --------------------------------------------------------------- the snapshot

_cache_lock = threading.Lock()
_cached: Optional[Tuple[float, Dict[str, Any]]] = None


def _database_mode() -> Optional[bool]:
    session = sys.modules.get("Database.session")
    check = getattr(session, "is_database_configured", None)
    try:
        return bool(check()) if callable(check) else None
    except Exception:  # noqa: BLE001
        return None


def _build() -> Dict[str, Any]:
    started = time.perf_counter()
    rss, peak = usage_feed.read_process_memory()
    container, limit = usage_feed.read_container_memory()
    groups = []
    measured = 0
    for group_id, group_label, specs in _GROUPS:
        items = [_measure_spec(spec) for spec in specs]
        items.sort(key=lambda item: item["bytes"], reverse=True)
        measured += sum(item["bytes"] for item in items)
        groups.append({"id": group_id, "label": group_label, "items": items})
    billable = container if container is not None else rss
    cpu = sys.modules.get("Service.BackendUsageService.cpu_usage_service")
    started_at = getattr(cpu, "_started_at", None)
    return {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "took_ms": round((time.perf_counter() - started) * 1000.0, 1),
        "database_mode": _database_mode(),
        "process": {
            "rss_bytes": rss,
            "peak_rss_bytes": peak,
            "container_bytes": container,
            "container_limit_bytes": limit,
            "python_threads": threading.active_count(),
            "uptime_seconds": round(time.time() - started_at) if isinstance(started_at, float) else None,
            "platform": sys.platform,
        },
        "billable_bytes": billable,
        "billable_source": "container" if container is not None else ("process" if rss is not None else None),
        "measured_bytes": measured,
        "unaccounted_bytes": max(0, billable - measured) if billable is not None else None,
        "groups": groups,
    }


def get_snapshot() -> Dict[str, Any]:
    """The RAM capsule's data. One measurement per _CACHE_SECONDS at most,
    shared by every caller."""
    global _cached
    with _cache_lock:
        now = time.monotonic()
        if _cached is not None and now - _cached[0] < _CACHE_SECONDS:
            return _cached[1]
        payload = _build()
        _cached = (time.monotonic(), payload)
        return payload
