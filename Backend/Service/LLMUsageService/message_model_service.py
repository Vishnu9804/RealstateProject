"""Message -> model log for the Dashboard's "Message to Model" tab: for every
WhatsApp message the property or requirement LLM stage actually read, what it
turned that message into (every property / requirement it produced, in full)
and what that message cost in tokens.

WHERE IT LIVES: a plain JSON-lines file OUTSIDE Backend/ (same --reload
reasoning as llm_usage_service), never in RAM. Only a tiny index — sequence
number, time and byte offset per entry — is kept in memory, so a thousand
logged messages cost this process tens of kilobytes, not the megabytes their
text and models would. Nothing touches the database: entries are captured at
the moment the LLM answers, from data the pipeline already has in hand.

RETENTION: 48 hours. Older entries are cut from the front of the file at most
every _PRUNE_EVERY_SECONDS.

READING: the dashboard asks for "everything after cursor N". The index turns
that into one seek to the first new entry and one read of the tail, and the
lines are spliced into the response as the JSON they already are — never
parsed and re-serialised. When nothing is new, no file is opened at all.

TOKENS PER MESSAGE: the LLM is called once per BATCH (up to 10 messages), and
the API reports tokens per call, not per message. So each call's tokens are
split across the messages it carried: input tokens in proportion to each
message's own block of the prompt (the shared instructions divided evenly),
output tokens in proportion to each message's own part of the reply. The
split is exact in total — one call's per-message numbers always add back up
to what that call was billed — and each entry also carries the whole call's
real numbers. A corrective re-ask (property stage only) is a second call,
split the same way over just the messages it re-asked about.

SAFETY: observe_batch runs on the pipeline's own flush thread, after the
batch's result is final, and never raises — a logging problem can never cost
a real property or requirement.
"""

from __future__ import annotations

import bisect
import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import Response

from Middleware import step_logger
from Service.BackendUsageService import usage_feed

_LOG_PATH = usage_feed.PROJECT_ROOT / "LLMUsage" / "message_model_log.jsonl"

_MAX_TEXT_CHARS = 6000
_PRUNE_EVERY_SECONDS = 600.0
# A message's block in the prompt is its text plus a few fixed lines
# (<<<MESSAGE id=...>>>, group:, text:, <<<END MESSAGE>>>).
_BLOCK_OVERHEAD_CHARS = 48

_lock = threading.Lock()
# Parallel lists, one position per stored entry, in file order.
_seqs: List[int] = []
_times: List[float] = []
_offsets: List[int] = []
_file_size = 0
_next_seq = 1
_last_prune = 0.0
_loaded = False
_pending_newline = False
_warned = False


def _warn_once(message: str) -> None:
    global _warned
    if not _warned:
        _warned = True
        step_logger.warn(f"{message} (the Message to Model tab may miss entries; the pipelines are unaffected.)")


def _parse_prefix(line: bytes) -> Optional[Tuple[int, float, int]]:
    """(seq, time, index where the JSON starts) of one stored line —
    "<seq>\\t<unix time>\\t<json>" — or None if it isn't one."""
    first = line.find(b"\t")
    if first <= 0:
        return None
    second = line.find(b"\t", first + 1)
    if second == -1:
        return None
    try:
        return int(line[:first]), float(line[first + 1 : second]), second + 1
    except ValueError:
        return None


# --------------------------------------------------------------- persistence


def load_from_disk() -> None:
    """Called once at startup (main.py); also run lazily on first use. Never
    raises — an unreadable file starts the tab empty."""
    with _lock:
        _load_locked()


def _load_locked() -> None:
    global _file_size, _next_seq, _loaded, _pending_newline
    _loaded = True
    _seqs.clear()
    _times.clear()
    _offsets.clear()
    _file_size = 0
    _pending_newline = False
    try:
        data = _LOG_PATH.read_bytes() if _LOG_PATH.exists() else b""
    except OSError as exc:
        step_logger.error(f"The message-to-model log ({_LOG_PATH}) could not be read: {exc!r}. Starting empty.")
        return
    position = 0
    while position < len(data):
        newline = data.find(b"\n", position)
        if newline == -1:
            break
        parsed = _parse_prefix(data[position:newline])
        if parsed is not None and (not _seqs or parsed[0] > _seqs[-1]):
            _seqs.append(parsed[0])
            _times.append(parsed[1])
            _offsets.append(position)
        position = newline + 1
    _file_size = position
    if position < len(data):
        # A line cut short by a crash mid-write. Truncated away so the next
        # append starts on a clean line; if that fails, the next append
        # starts with a newline instead so it can't glue onto the fragment.
        try:
            with open(_LOG_PATH, "r+b") as handle:
                handle.truncate(position)
        except OSError:
            _file_size = len(data)
            _pending_newline = True
    _next_seq = (_seqs[-1] + 1) if _seqs else 1
    _prune_locked(time.time(), force=True)
    step_logger.info(
        f"Message-to-model log loaded: {len(_seqs)} entr{'y' if len(_seqs) == 1 else 'ies'} "
        f"from the last 48 hours ({_LOG_PATH})."
    )


def _prune_locked(now: float, force: bool = False) -> None:
    global _file_size, _last_prune, _pending_newline
    if not _seqs or (not force and now - _last_prune < _PRUNE_EVERY_SECONDS):
        return
    _last_prune = now
    floor = now - usage_feed.RETENTION_SECONDS
    keep = 0
    while keep < len(_times) and _times[keep] < floor:
        keep += 1
    if keep == 0:
        return
    base = _offsets[keep] if keep < len(_offsets) else _file_size
    try:
        with open(_LOG_PATH, "rb") as handle:
            handle.seek(base)
            tail = handle.read(max(0, _file_size - base))
        temp_path = _LOG_PATH.with_name(_LOG_PATH.name + ".tmp")
        with open(temp_path, "wb") as handle:
            handle.write(tail)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, _LOG_PATH)
    except OSError as exc:
        _warn_once(f"Could not trim the message-to-model log ({_LOG_PATH}): {exc!r}")
        return
    del _seqs[:keep]
    del _times[:keep]
    del _offsets[:keep]
    for index in range(len(_offsets)):
        _offsets[index] -= base
    _file_size = len(tail)
    _pending_newline = False


# ------------------------------------------------------------------ write path


def _split(total: int, weights: List[float]) -> List[int]:
    """Splits `total` in proportion to `weights` into whole numbers that add
    back up to exactly `total` (largest remainder)."""
    count = len(weights)
    if count == 0:
        return []
    if total <= 0:
        return [0] * count
    weight_sum = sum(weights)
    if weight_sum <= 0:
        weights = [1.0] * count
        weight_sum = float(count)
    raw = [total * weight / weight_sum for weight in weights]
    parts = [int(value) for value in raw]
    leftover = total - sum(parts)
    for index in sorted(range(count), key=lambda i: raw[i] - parts[i], reverse=True)[:leftover]:
        parts[index] += 1
    return parts


def observe_batch(
    site: str,
    model: str,
    messages: Sequence[Any],
    calls: Sequence[Dict[str, Any]],
    outcomes: Dict[str, Dict[str, Any]],
    models_by_message: Dict[str, List[Dict[str, Any]]],
) -> None:
    """Logs one structured batch: one entry per message.

    `calls` — one {"message_ids", "input", "output", "prompt_chars"} per
    successful LLM call the batch took. `outcomes` — per message id:
    {"outcome", "note", "output_chars"} ("converted" / "skipped" /
    "rerouted"); a message with none is logged as "missing" (the model
    returned nothing for it). `models_by_message` — the records produced,
    already reduced to plain JSON-able dicts. Never raises."""
    global _next_seq, _file_size, _pending_newline
    try:
        if not messages or not calls:
            return
        now = time.time()
        by_id: Dict[str, Any] = {}
        for message in messages:
            by_id.setdefault(message.message_id, message)

        tokens_in = {message_id: 0 for message_id in by_id}
        tokens_out = {message_id: 0 for message_id in by_id}
        for call in calls:
            ids = [message_id for message_id in dict.fromkeys(call.get("message_ids") or by_id) if message_id in by_id]
            if not ids:
                continue
            blocks = [
                len(by_id[message_id].text or "")
                + len(by_id[message_id].chat_name or "")
                + len(message_id)
                + _BLOCK_OVERHEAD_CHARS
                for message_id in ids
            ]
            shared = max(0, int(call.get("prompt_chars") or 0) - sum(blocks))
            input_parts = _split(int(call.get("input") or 0), [shared / len(ids) + block for block in blocks])
            output_parts = _split(
                int(call.get("output") or 0),
                [max(1, int((outcomes.get(message_id) or {}).get("output_chars") or 0)) for message_id in ids],
            )
            for message_id, input_part, output_part in zip(ids, input_parts, output_parts):
                tokens_in[message_id] += input_part
                tokens_out[message_id] += output_part

        batch_info = {
            "messages": len(by_id),
            "calls": len(calls),
            "input": sum(int(call.get("input") or 0) for call in calls),
            "output": sum(int(call.get("output") or 0) for call in calls),
            "retry": len(calls) > 1,
        }

        with _lock:
            if not _loaded:
                _load_locked()
            built: List[Tuple[int, bytes]] = []
            for message_id, message in by_id.items():
                outcome = outcomes.get(message_id) or {
                    "outcome": "missing",
                    "note": "The model returned nothing for this message, so it was dropped from the batch.",
                }
                text = message.text or ""
                entry = {
                    "id": uuid.uuid4().hex,
                    "at": round(now, 3),
                    "site": site,
                    "model": model,
                    "message_id": message_id,
                    "received_at": message.received_at.isoformat() if message.received_at else None,
                    "group": message.chat_name,
                    "chat_type": message.chat_type,
                    "sender": message.sender_name,
                    "sender_saved": message.sender_saved_name,
                    "sender_phone": message.sender_phone,
                    "text": text[:_MAX_TEXT_CHARS],
                    "text_truncated": len(text) > _MAX_TEXT_CHARS,
                    "outcome": outcome.get("outcome") or "missing",
                    "note": outcome.get("note"),
                    "models": models_by_message.get(message_id) or [],
                    "tokens": {
                        "input": tokens_in[message_id],
                        "output": tokens_out[message_id],
                        "total": tokens_in[message_id] + tokens_out[message_id],
                    },
                    "batch": batch_info,
                }
                seq = _next_seq
                _next_seq += 1
                payload = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
                built.append((seq, f"{seq}\t{now:.3f}\t{payload}\n".encode("utf-8")))

            prefix = b"\n" if _pending_newline else b""
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_LOG_PATH, "ab") as handle:
                handle.seek(0, os.SEEK_END)
                start = handle.tell()
                handle.write(prefix + b"".join(line for _seq, line in built))
            if start < _file_size:
                # The file was removed or cut from outside this process —
                # the index no longer describes it, so it starts over here.
                _seqs.clear()
                _times.clear()
                _offsets.clear()
            offset = start + len(prefix)
            for seq, line in built:
                _seqs.append(seq)
                _times.append(now)
                _offsets.append(offset)
                offset += len(line)
            _file_size = offset
            _pending_newline = False
            _prune_locked(now)
    except Exception as exc:  # noqa: BLE001
        _warn_once(f"Could not log a {site} batch for the Message to Model tab: {exc!r}")


# ------------------------------------------------------------------- read path


def get_changes(cursor: Optional[str]) -> Response:
    """Every entry logged after `cursor` within the last 48 hours, as
    {"cursor": ..., "items": [...]} — see the module docstring."""
    since = usage_feed.parse_cursor(cursor)
    floor = time.time() - usage_feed.RETENTION_SECONDS
    parts: List[bytes] = []
    with _lock:
        if not _loaded:
            _load_locked()
        position = _next_seq - 1
        start = bisect.bisect_right(_seqs, since)
        while start < len(_seqs) and _times[start] < floor:
            start += 1
        if start < len(_seqs):
            try:
                with open(_LOG_PATH, "rb") as handle:
                    handle.seek(_offsets[start])
                    data = handle.read(max(0, _file_size - _offsets[start]))
            except OSError as exc:
                _warn_once(f"Could not read the message-to-model log ({_LOG_PATH}): {exc!r}")
                data = b""
            for line in data.split(b"\n"):
                parsed = _parse_prefix(line)
                if parsed is None:
                    continue
                seq, at, json_start = parsed
                body = line[json_start:]
                # Cheap guard against a damaged line: only a complete JSON
                # object is ever spliced into the response.
                if seq <= since or at < floor or not body.startswith(b"{") or not body.endswith(b"}"):
                    continue
                parts.append(body)
    content = (
        b'{"cursor":'
        + json.dumps(usage_feed.make_cursor(position)).encode("utf-8")
        + b',"items":['
        + b",".join(parts)
        + b"]}"
    )
    return Response(content=content, media_type="application/json", headers={"Cache-Control": "no-store"})
