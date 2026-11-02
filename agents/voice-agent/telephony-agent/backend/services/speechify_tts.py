"""Speechify Simba 3.5 streaming TTS client.

Endpoint:  POST https://api.speechify.ai/v1/audio/stream
Auth:      Authorization: Bearer <api key>
Request:   {"input": ..., "voice_id": ..., "model": "simba-3.5",
            "language": "en-IN", "output_format": "ulaw_8000"}
Response:  audio/basic — raw 8 kHz μ-law, exactly Plivo's telephony format.

The HTTP endpoint takes the complete text for one request and streams the
audio back, so this client splits the agent's incremental text into
sentence-sized segments and synthesizes them concurrently.  A single pump
forwards the segment audio to the caller in order: the first sentence starts
playing as soon as it is ready while later sentences are already being
generated, keeping time-to-first-audio low without waiting for the full LLM
answer.

The public interface mirrors :class:`InworldTTS` so the agent can switch
providers without changing its turn orchestration.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

log = logging.getLogger("speechify_tts")

STREAM_PATH = "/v1/audio/stream"
MAX_CONCURRENT_REQUESTS = 2  # account plan allows 3; leave one slot of headroom
SEGMENT_QUEUE_SIZE = 64
AUDIO_QUEUE_SIZE = 600

# The limit is per Speechify account, not per call, so every SpeechifyTTS
# instance in this process shares the same request slots.  Semaphores are
# created per running loop so the test harness can use its own loop.
_REQUEST_SLOTS: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}


def _request_slots() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    semaphore = _REQUEST_SLOTS.get(loop)
    if semaphore is None:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        _REQUEST_SLOTS[loop] = semaphore
    return semaphore


def _server_timing(header: str, name: str) -> float | None:
    match = re.search(rf"{name};dur=([\d.]+)", header or "")
    return float(match.group(1)) if match else None


class SpeechifyTTSError(RuntimeError):
    pass


def _put_nowait(queue: asyncio.Queue, item: Any) -> None:
    """Put without blocking, dropping the oldest item if the queue is full."""
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            pass


def _error_message(status_code: int, body: bytes) -> str:
    text = body.decode("utf-8", "replace").strip()
    detail = ""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("code") or "")
        elif isinstance(error, str):
            detail = error
        if not detail:
            detail = str(data.get("message") or data.get("detail") or "")
    if not detail:
        detail = text[:300]
    return f"Speechify HTTP {status_code}: {detail or 'request failed'}"


@dataclass
class _Segment:
    queue: asyncio.Queue = field(
        default_factory=lambda: asyncio.Queue(maxsize=SEGMENT_QUEUE_SIZE)
    )


@dataclass
class _Context:
    voice_id: str
    segment_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    audio_queue: asyncio.Queue = field(
        default_factory=lambda: asyncio.Queue(maxsize=AUDIO_QUEUE_SIZE)
    )
    pump: asyncio.Task | None = None
    tasks: set[asyncio.Task] = field(default_factory=set)
    chars: int = 0
    closed: bool = False
    abandoned: bool = False


class SpeechifyTTS:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.speechify.ai",
        model: str = "simba-3.5",
        voice_id: str = "mihika",
        language: str = "en-IN",
        output_format: str = "ulaw_8000",
        sample_rate: int = 8000,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        if output_format.lower() != "ulaw_8000":
            raise ValueError("SpeechifyTTS only supports output_format='ulaw_8000'")
        if int(sample_rate) != 8000:
            raise ValueError("SpeechifyTTS ulaw_8000 output is fixed at 8 kHz")

        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.voice_id = voice_id
        self.language = language.strip()
        self.output_format = output_format.lower()
        self.sample_rate = int(sample_rate)
        self.on_event = on_event

        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()
        self._contexts: dict[str, _Context] = {}
        self._chars: dict[str, int] = {}
        self._closing = False

    # ------------------------------------------------------------------ API
    async def ensure_connected(self) -> None:
        """Create the pooled HTTP client used for all streaming requests."""
        if not self.api_key:
            raise SpeechifyTTSError("SPEECHIFY_API_KEY is not set")
        if self._closing:
            raise SpeechifyTTSError("Speechify client is closed")
        if self._client is not None and not self._client.is_closed:
            return
        async with self._client_lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.AsyncClient(
                    timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0),
                    limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "Accept": "audio/basic",
                    },
                )

    async def create_context(
        self,
        context_id: str,
        *,
        voice_id: str | None = None,
    ) -> None:
        await self.ensure_connected()
        existing = self._contexts.pop(context_id, None)
        if existing is not None:
            await self._cancel_context(existing)

        context = _Context(voice_id=voice_id or self.voice_id)
        self._contexts[context_id] = context
        self._chars[context_id] = 0
        context.pump = asyncio.create_task(
            self._pump(context_id, context), name=f"speechify-pump-{context_id}"
        )
        log.info(
            "speechify_context_created",
            extra={"context_id": context_id, "voice_id": context.voice_id, "model": self.model},
        )

    async def send_text(self, context_id: str, text: str) -> None:
        if not text or not text.strip():
            return
        context = self._contexts.get(context_id)
        if context is None:
            raise SpeechifyTTSError(f"Unknown TTS context {context_id}")
        if context.abandoned:
            return

        context.chars += len(text)
        self._chars[context_id] = context.chars
        segment = _Segment()
        await context.segment_queue.put(segment)
        task = asyncio.create_task(
            self._synthesize(context, segment, text),
            name=f"speechify-segment-{context_id}",
        )
        context.tasks.add(task)
        task.add_done_callback(context.tasks.discard)

    async def close_context(self, context_id: str) -> None:
        context = self._contexts.get(context_id)
        if context is None or context.closed:
            return
        context.closed = True
        await context.segment_queue.put(None)

    async def audio(self, context_id: str):
        """Yield μ-law bytes until every segment for the context is played."""
        context = self._contexts.get(context_id)
        if context is None:
            raise SpeechifyTTSError(f"Unknown TTS context {context_id}")
        finished = False
        try:
            while True:
                item = await context.audio_queue.get()
                if item is None:
                    finished = True
                    break
                if isinstance(item, BaseException):
                    finished = True
                    raise item
                if context.abandoned:
                    continue
                yield item
        finally:
            if finished:
                await self._cleanup_context(context_id, context)

    async def abandon_context(self, context_id: str) -> None:
        """Barge-in: stop consuming/synthesizing and free the HTTP streams."""
        context = self._contexts.pop(context_id, None)
        if context is None:
            return
        context.abandoned = True
        log.debug("speechify_context_abandoned", extra={"context_id": context_id})
        _put_nowait(context.audio_queue, None)
        await self._cancel_context(context)

    def processed_characters(self, context_id: str) -> int:
        return self._chars.get(context_id, 0)

    async def aclose(self) -> None:
        self._closing = True
        for context_id in list(self._contexts):
            await self.abandon_context(context_id)
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass

    # -------------------------------------------------------------- internal
    async def _synthesize(self, context: _Context, segment: _Segment, text: str) -> None:
        error: Exception | None = None
        try:
            await self.ensure_connected()
            assert self._client is not None
            payload = {
                "input": text,
                "voice_id": context.voice_id,
                "model": self.model,
                "output_format": self.output_format,
            }
            if self.language:
                payload["language"] = self.language
            started = time.perf_counter()
            async with _request_slots():
                async with self._client.stream(
                    "POST", f"{self.base_url}{STREAM_PATH}", json=payload
                ) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        raise SpeechifyTTSError(
                            _error_message(response.status_code, body)
                        )
                    first_chunk = True
                    async for chunk in response.aiter_bytes():
                        if context.abandoned:
                            return
                        if not chunk:
                            continue
                        if first_chunk:
                            first_chunk = False
                            server_timing = response.headers.get("server-timing", "")
                            log.info(
                                "speechify_first_audio",
                                extra={
                                    "chars": len(text),
                                    "voice_id": context.voice_id,
                                    "request_to_first_audio_ms": round(
                                        (time.perf_counter() - started) * 1000, 1
                                    ),
                                    "server_ttfb_ms": _server_timing(server_timing, "ttfb"),
                                    "model_ms": _server_timing(server_timing, "model"),
                                },
                            )
                        await segment.queue.put(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider boundary
            error = exc
            log.warning(
                "speechify_synthesis_failed",
                extra={
                    "error": str(exc),
                    "chars": len(text),
                    "voice_id": context.voice_id,
                },
            )
        finally:
            if error is not None:
                _put_nowait(segment.queue, SpeechifyTTSError(str(error)))
            _put_nowait(segment.queue, None)

    async def _pump(self, context_id: str, context: _Context) -> None:
        """Forward segment audio to the context queue in synthesis order."""
        try:
            while True:
                segment = await context.segment_queue.get()
                if segment is None:
                    break
                while True:
                    item = await segment.queue.get()
                    if item is None:
                        break
                    if isinstance(item, BaseException):
                        raise item
                    if not context.abandoned:
                        await context.audio_queue.put(item)
            if not context.abandoned:
                _put_nowait(context.audio_queue, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider boundary
            if not context.abandoned:
                log.warning(
                    "speechify_stream_failed",
                    extra={"context_id": context_id, "error": str(exc)},
                )
                _put_nowait(context.audio_queue, SpeechifyTTSError(str(exc)))
                _put_nowait(context.audio_queue, None)

    async def _cleanup_context(self, context_id: str, context: _Context) -> None:
        context.abandoned = True
        self._contexts.pop(context_id, None)
        await self._cancel_context(context, include_pump=False)

    async def _cancel_context(
        self, context: _Context, *, include_pump: bool = True
    ) -> None:
        tasks = [task for task in context.tasks if not task.done()]
        if (
            include_pump
            and context.pump is not None
            and not context.pump.done()
            and context.pump is not asyncio.current_task()
        ):
            tasks.append(context.pump)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
