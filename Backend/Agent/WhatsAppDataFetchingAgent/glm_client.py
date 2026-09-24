"""Shared, batch-agnostic transport for the Z.ai (GLM) chat-completions API:
one streamed request, the runaway/repetition guards, and the retry policy.

Why this exists as its own module rather than being imported out of
property_structurer.py: the property structuring stage is the single most
load-bearing piece of this application and its copy of this logic is
hand-tuned against real, observed production failures (see its own
comments). Extracting it from there would have meant rewriting the property
pipeline to add a second, unrelated feature — a bad trade. So the requirement
structuring stage gets its own copy of the same, proven policy here, and
property_structurer.py is left byte-for-byte alone. If the two ever need to
converge, this module is the target to converge ON, not a new third one.

Every constant below is carried over from the property structurer's own
measurements against this exact API:

  - Streamed, always. A non-streamed call is one long silence until the
    whole reply is generated, so the read timeout has to cover full
    generation time and a slow answer is indistinguishable from a dead
    connection. Streamed, `read` only ever measures the gap between two
    chunks.
  - A wall-clock ceiling (_MAX_STREAM_SECONDS) for a stream that keeps
    dribbling tokens forever, which a per-chunk timeout can never catch.
  - A character ceiling (_MAX_CONTENT_CHARS) and a repetition-loop detector
    for degenerate generation, which this model does fall into.
  - Retries on transient failures only (timeout/drop, empty/overlong
    stream, 429/5xx) — never on a 4xx, which would fail identically and
    cost identically on every retry.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Dict, List, Optional, Tuple

import httpx

from Agent.WhatsAppDataFetchingAgent import glm_gate
from Config.settings import get_settings
from Middleware import step_logger
from Service.LLMUsageService import llm_usage_service

# One httpx client per lane, because a client carries its API key in its
# Authorization header — see lane_for / _api_key_for_lane.
_clients: Dict[str, httpx.Client] = {}
_clients_lock = threading.Lock()

_MAX_ATTEMPTS = 4
_RETRY_BACKOFF_SECONDS = [3.0, 10.0, 30.0]

# A 429 is counted separately from the attempts above, and allowed more of
# them, because it is a rejection rather than a failure: Z.ai turned the
# request away before the model ran, so it cost no tokens and proves nothing
# about the request itself. Spending the 4-attempt budget on rate limits was
# what abandoned real batches after ~43 seconds of a rate-limit window that
# routinely lasts longer than that. Paired with the process-wide gate in
# glm_gate.py, which is what stops us causing the 429 in the first place.
_MAX_RATE_LIMIT_RETRIES = 6
_MAX_STREAM_SECONDS = 600.0
_MAX_CONTENT_CHARS = 60_000
_LOOP_CHECK_EVERY_CHUNKS = 150
_LOOP_WINDOW_CHARS = 3000
_LOOP_MIN_CHARS = 400
_LOOP_MIN_REPEATS = 6


class TransientCompletionError(Exception):
    """A response that arrived but is unusable (empty, or a stream that ran
    past _MAX_STREAM_SECONDS, or runaway/looping output). Worth exactly the
    same retry treatment as a dropped connection — unlike a 4xx, it may well
    succeed next time."""


def looks_like_repetition_loop(text: str) -> bool:
    """True when the tail of `text` is one short phrase repeated over and
    over — a model stuck in a degenerate generation loop.

    Deliberately conservative, because a false positive throws away a real
    extraction: the repeating unit must fill at least _LOOP_MIN_CHARS of
    contiguous tail AND repeat at least _LOOP_MIN_REPEATS times. Ordinary
    output never does this — even a long list of similar requirements
    differs in area/budget/BHK every entry."""
    tail = text[-_LOOP_WINDOW_CHARS:]
    if len(tail) < _LOOP_MIN_CHARS:
        return False
    for unit_length in range(3, 81):
        unit = tail[-unit_length:]
        if not unit.strip():
            continue
        repeats = 1
        while (
            len(tail) >= unit_length * (repeats + 1)
            and tail[-unit_length * (repeats + 1) : -unit_length * repeats] == unit
        ):
            repeats += 1
        if repeats >= _LOOP_MIN_REPEATS and repeats * unit_length >= _LOOP_MIN_CHARS:
            return True
    return False


def lane_for(lane: str) -> str:
    """The lane a call for `lane` actually runs on. "inquiry" only gets its
    own lane (its own gate AND its own httpx client) when ZAI_API_KEY_INQUIRY
    is set; while it is blank, inquiry calls use the property key, so they
    must also queue on the property gate — two gates in front of one Z.ai
    account would let the two workloads collide again. Everything that is
    not "inquiry" runs on the property lane, as it always did."""
    if lane == glm_gate.LANE_INQUIRY and get_settings().zai_api_key_inquiry:
        return glm_gate.LANE_INQUIRY
    return glm_gate.LANE_PROPERTY


def _api_key_for_lane(lane: str) -> str:
    settings = get_settings()
    if lane == glm_gate.LANE_INQUIRY:
        return settings.zai_api_key_inquiry
    return settings.zai_api_key_property


def has_api_key(lane: str) -> bool:
    """True when a call for `lane` has a key to send with (the inquiry lane
    counts its fallback to the property key)."""
    return bool(_api_key_for_lane(lane_for(lane)))


def _get_client(lane: str = glm_gate.LANE_PROPERTY) -> httpx.Client:
    lane = lane_for(lane)
    client = _clients.get(lane)
    if client is not None:
        return client
    with _clients_lock:
        client = _clients.get(lane)
        if client is None:
            api_key = _api_key_for_lane(lane)
            if not api_key:
                variable = "ZAI_API_KEY_INQUIRY" if lane == glm_gate.LANE_INQUIRY else "ZAI_API_KEY_PROPERTY"
                raise RuntimeError(f"{variable} is not set — add it to Backend/.env before the LLM stage can run.")
            client = httpx.Client(
                base_url=get_settings().zai_base_url,
                headers={"Authorization": f"Bearer {api_key}"},
                # `read` is the gap between two streamed chunks, not total
                # generation time — see the module docstring.
                timeout=httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=15.0),
                # Batches arrive at most once a minute and often far less often,
                # so a pooled connection is usually cold by the time the next one
                # needs it — and reusing a silently-dropped idle socket costs a
                # full `read` timeout before httpx gives up.
                limits=httpx.Limits(max_keepalive_connections=5, keepalive_expiry=30.0),
            )
            _clients[lane] = client
    return client


def build_request_body(system_prompt: str, user_prompt: str, max_tokens: int = 16000) -> dict:
    """The request shape both structuring stages use. temperature is
    deliberately 0.2 and NOT 0.0: greedy decoding on a small model is highly
    prone to degenerate repetition (once it emits a phrase twice, that
    phrase is by construction the most likely continuation again), which was
    the direct cause of runaway generations, blown stream budgets and wrong
    extraction counts. `thinking` is disabled because chain-of-thought adds
    significant latency for no benefit on a fixed-shape extraction task."""
    return {
        "model": get_settings().zai_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
        "stream": True,
        # Asks the API to include a final `usage` object in the stream (the
        # standard OpenAI-compatible mechanism for a streamed response to
        # still report token counts) — purely additive: an API that already
        # sends usage without it is unaffected, and one that doesn't
        # recognise the field simply ignores it like any other unrecognised
        # JSON key. Feeds the Dashboard's LLM Cost tab; see _stream_completion.
        "stream_options": {"include_usage": True},
    }


def _stream_completion(client: httpx.Client, request_body: dict) -> Tuple[str, Optional[dict]]:
    """Sends one request and assembles the streamed reply into the same
    single content string a non-streamed call would have returned, plus
    whatever `usage` object the API included (None if it never sent one).
    Raises TransientCompletionError for an empty stream, an over-long
    stream, a runaway character count, or a detected repetition loop — all
    retryable, unlike a 4xx."""
    started = time.monotonic()
    pieces: List[str] = []
    content_chars = 0
    usage: Optional[dict] = None
    with client.stream("POST", "chat/completions", json=request_body) as response:
        if response.status_code >= 400:
            # The body of a streamed error response hasn't been read yet;
            # read it first so raise_for_status's message carries Z.ai's
            # actual explanation rather than an empty stream.
            response.read()
            response.raise_for_status()
        for line in response.iter_lines():
            if time.monotonic() - started > _MAX_STREAM_SECONDS:
                raise TransientCompletionError(f"stream ran past {_MAX_STREAM_SECONDS:.0f}s without finishing")
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if payload == "[DONE]":
                break
            try:
                frame = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(frame, dict):
                # The final frame of a stream carrying `stream_options:
                # {"include_usage": true}` has empty `choices` and this
                # `usage` object instead — captured here, BEFORE the
                # choices/delta lookup below (which would otherwise skip
                # straight past it), so it survives to the return statement.
                frame_usage = frame.get("usage")
                if isinstance(frame_usage, dict):
                    usage = frame_usage
            try:
                delta = frame["choices"][0]["delta"]
            except (KeyError, IndexError, TypeError):
                # A keep-alive/usage-only frame, not a content chunk.
                continue
            piece = delta.get("content")
            if piece:
                pieces.append(piece)
                content_chars += len(piece)
                if content_chars > _MAX_CONTENT_CHARS:
                    raise TransientCompletionError(
                        f"the model produced over {_MAX_CONTENT_CHARS:,} characters for this batch "
                        "— runaway generation, far beyond any real extraction"
                    )
                if len(pieces) % _LOOP_CHECK_EVERY_CHUNKS == 0 and looks_like_repetition_loop("".join(pieces[-400:])):
                    raise TransientCompletionError(
                        "the model fell into a repetition loop (same phrase emitted over and over) — "
                        "abandoning this attempt instead of waiting out a reply that cannot parse"
                    )

    content = "".join(pieces)
    if not content.strip():
        raise TransientCompletionError("the stream completed without any content")
    return content, usage


def _record_usage(site: str, model: str, usage: Optional[dict]) -> Tuple[int, int]:
    """Best-effort: feeds this call's token counts to the Dashboard's LLM
    Cost tab. Never raises — a tracking failure must never cost a real
    requirement. `usage` is None when the API didn't send one (the call
    still counts, just with 0 tokens attributed — see the service's own
    docstring on why). Returns the (input, output) tokens it recorded."""
    try:
        usage = usage or {}
        input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        llm_usage_service.observe_call(site, model, input_tokens, output_tokens)
        return input_tokens, output_tokens
    except Exception as exc:  # noqa: BLE001
        step_logger.warn(f"Could not record LLM usage for a {site} GLM call: {exc!r}")
        return 0, 0


def post_with_retries(
    request_body: dict,
    description: str,
    site: str,
    usage_sink: Optional[List[dict]] = None,
    failure: Optional[dict] = None,
    priority: bool = False,
    lane: str = glm_gate.LANE_PROPERTY,
) -> Optional[str]:
    """Sends `request_body` and returns the model's raw reply text, retrying
    on transient failures only. Returns None once every attempt has failed.
    `description` is used purely in log lines (e.g. "a requirement batch of
    4"). `site` identifies the calling pipeline stage for the LLM Cost
    dashboard (e.g. "requirement") — recorded once, only on a successful
    completion; a failed/retried attempt records nothing, since no usage is
    known for it.

    Every request goes out through glm_gate.slot, the process-wide gate that
    keeps this backend from having several GLM calls in flight at once. That
    is the actual fix for the repeated 429s: they were our own concurrent
    batches colliding, not Z.ai being unavailable.

    Two separate budgets, because the two failures are not alike:
      - _MAX_ATTEMPTS for failures that reached the model (a drop, a timeout,
        an unusable stream) — each one costs tokens, so the budget is small;
      - _MAX_RATE_LIMIT_RETRIES for HTTP 429, which is a free rejection and
        gets a longer, server-directed wait (see glm_gate.note_rate_limited).

    `usage_sink`, when given, receives one {"input", "output",
    "prompt_chars"} entry for the successful call. The caller knows which
    messages the call carried and uses it to split the tokens per message
    for the Dashboard's Message to Model tab; leaving it out changes
    nothing.

    `failure`, when given, has ["reason"] set to a short human explanation if
    this returns None — the caller passes it on to the durable retry queue so
    a held batch records WHY it is waiting.

    `priority`, when True, lets this call jump ahead of any already-queued
    non-priority call for the NEXT slot once the current one frees — see
    glm_gate.slot. Only inquiry_classifier.py sets this; every other caller
    keeps the default and queues FIFO exactly as before.

    `lane` picks the Z.ai account (API key) and the gate the call runs on —
    see lane_for. The default is the property lane, so every caller that
    doesn't pass it behaves exactly as it did with the single shared key.
    Only inquiry_classifier.py passes glm_gate.LANE_INQUIRY."""
    lane = lane_for(lane)
    client = _get_client(lane)
    model = request_body.get("model") or get_settings().zai_model

    attempt = 0
    rate_limit_retries = 0

    while True:
        try:
            with glm_gate.slot(description, priority=priority, lane=lane):
                content, usage = _stream_completion(client, request_body)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429:
                rate_limit_retries += 1
                explanation = glm_gate.describe(exc.response)
                out_of_allowance = glm_gate.is_daily_limit(exc.response)
                if out_of_allowance or rate_limit_retries > _MAX_RATE_LIMIT_RETRIES:
                    step_logger.error(
                        f"GLM request for {description} is still rate-limited after "
                        f"{rate_limit_retries - 1} retr(ies) — {explanation}. Handing this batch to "
                        "the durable retry queue, which will keep trying until it goes through."
                    )
                    return _failed(failure, f"HTTP 429 — {explanation}")
                wait_for = glm_gate.note_rate_limited(exc.response, rate_limit_retries, lane=lane)
                step_logger.warn(
                    f"GLM request for {description} was rate-limited (HTTP 429, rate-limit retry "
                    f"{rate_limit_retries}/{_MAX_RATE_LIMIT_RETRIES}) — {explanation}. Pausing every "
                    f"GLM request for {wait_for:.0f}s, then trying again. Nothing is dropped."
                )
                time.sleep(wait_for)
                continue
            attempt += 1
            if status < 500 or attempt >= _MAX_ATTEMPTS:
                step_logger.error(
                    f"GLM request failed for {description} (attempt {attempt}/{_MAX_ATTEMPTS}, HTTP {status}): {exc!r}"
                )
                return _failed(failure, f"HTTP {status} from Z.ai")
            step_logger.warn(
                f"GLM request got HTTP {status} for {description} (attempt {attempt}/{_MAX_ATTEMPTS}) — "
                "retrying, this is a Z.ai-side status that's worth another try, not a request we're sending wrong."
            )
        except (httpx.TimeoutException, httpx.TransportError, TransientCompletionError) as exc:
            attempt += 1
            if attempt >= _MAX_ATTEMPTS:
                step_logger.error(
                    f"GLM request failed for {description} (attempt {attempt}/{_MAX_ATTEMPTS}): {exc!r}"
                )
                return _failed(failure, f"the request kept timing out or dropping: {exc!r}")
            step_logger.warn(
                f"GLM request timed out/dropped for {description} (attempt {attempt}/{_MAX_ATTEMPTS}) — "
                f"retrying rather than losing these records: {exc!r}"
            )
        except Exception as exc:  # noqa: BLE001
            # Anything else (bad API key, DNS failure, ...) will fail the
            # exact same way on every retry — burning more paid calls to
            # confirm that would just be wasted spend. It still goes to the
            # durable queue: a key or a DNS entry gets fixed, and the batch
            # then goes through by itself.
            step_logger.error(f"GLM request failed for {description}: {exc!r}")
            return _failed(failure, f"the request could not be sent: {exc!r}")
        else:
            input_tokens, output_tokens = _record_usage(site, model, usage)
            if usage_sink is not None:
                usage_sink.append(
                    {
                        "input": input_tokens,
                        "output": output_tokens,
                        "prompt_chars": sum(
                            len(str(message.get("content") or "")) for message in request_body.get("messages") or []
                        ),
                    }
                )
            return content

        time.sleep(_RETRY_BACKOFF_SECONDS[min(attempt, len(_RETRY_BACKOFF_SECONDS)) - 1])


def _failed(failure: Optional[dict], reason: str) -> None:
    """Records why the call gave up, for the durable retry queue's log, and
    returns None so callers can `return _failed(...)` in one line."""
    if failure is not None:
        failure["reason"] = reason
    return None
