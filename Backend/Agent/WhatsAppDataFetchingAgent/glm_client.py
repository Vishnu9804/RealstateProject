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
import time
from typing import List, Optional

import httpx

from Config.settings import get_settings
from Middleware import step_logger

_client: Optional[httpx.Client] = None

_MAX_ATTEMPTS = 4
_RETRY_BACKOFF_SECONDS = [3.0, 10.0, 30.0]
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


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        api_key = get_settings().zai_api_key
        if not api_key:
            raise RuntimeError("ZAI_API_KEY is not set — add it to Backend/.env before the LLM stage can run.")
        _client = httpx.Client(
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
    return _client


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
    }


def _stream_completion(client: httpx.Client, request_body: dict) -> str:
    """Sends one request and assembles the streamed reply into the same
    single content string a non-streamed call would have returned. Raises
    TransientCompletionError for an empty stream, an over-long stream, a
    runaway character count, or a detected repetition loop — all retryable,
    unlike a 4xx."""
    started = time.monotonic()
    pieces: List[str] = []
    content_chars = 0
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
                delta = json.loads(payload)["choices"][0]["delta"]
            except (json.JSONDecodeError, KeyError, IndexError, TypeError):
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
    return content


def post_with_retries(request_body: dict, description: str) -> Optional[str]:
    """Sends `request_body` and returns the model's raw reply text, retrying
    up to _MAX_ATTEMPTS times on transient failures only. Returns None once
    every attempt has failed. `description` is used purely in log lines
    (e.g. "a requirement batch of 4")."""
    client = _get_client()

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return _stream_completion(client, request_body)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            transient = status == 429 or status >= 500
            if not transient or attempt == _MAX_ATTEMPTS:
                step_logger.error(
                    f"GLM request failed for {description} (attempt {attempt}/{_MAX_ATTEMPTS}, HTTP {status}): {exc!r}"
                )
                return None
            step_logger.warn(
                f"GLM request got HTTP {status} for {description} (attempt {attempt}/{_MAX_ATTEMPTS}) — "
                "retrying, this is a Z.ai-side status that's worth another try, not a request we're sending wrong."
            )
        except (httpx.TimeoutException, httpx.TransportError, TransientCompletionError) as exc:
            if attempt == _MAX_ATTEMPTS:
                step_logger.error(
                    f"GLM request failed for {description} (attempt {attempt}/{_MAX_ATTEMPTS}): {exc!r}"
                )
                return None
            step_logger.warn(
                f"GLM request timed out/dropped for {description} (attempt {attempt}/{_MAX_ATTEMPTS}) — "
                f"retrying rather than losing these records: {exc!r}"
            )
        except Exception as exc:  # noqa: BLE001
            # Anything else (bad API key, DNS failure, ...) will fail the
            # exact same way on every retry — burning more paid calls to
            # confirm that would just be wasted spend.
            step_logger.error(f"GLM request failed for {description}: {exc!r}")
            return None

        time.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])

    return None
