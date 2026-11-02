"""Sarvam Bulbul v4-flash streaming TTS client (WebSocket).

Endpoint:  wss://api.sarvam.ai/text-to-speech/ws?model=bulbul:v4-flash&send_completion_event=true
Auth:      Api-Subscription-Key header
Protocol:  {"type":"config"} first, then {"type":"text"} / {"type":"flush"}.
           Responses: {"type":"audio","data":{"audio":<base64>}},
           {"type":"event","data":{"event_type":"final"}} (one per flush) and
           {"type":"error"}.
Audio:     output_audio_codec="mulaw" at speech_sample_rate=8000 is Plivo's
           native telephony format, so nothing is transcoded.

The protocol has no context IDs and no cancel message, so this client keeps one
persistent connection, runs one context at a time, and ends a context after one
``final`` event per flush.  Barge-in closes the socket (the only way to stop
queued synthesis) and reconnects in the background so the next turn is warm.

The public interface mirrors :class:`InworldTTS` / :class:`SpeechifyTTS`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

import websockets

log = logging.getLogger("sarvam_tts")

WS_BASE_URL = "wss://api.sarvam.ai/text-to-speech/ws"
MAX_TEXT_CHUNK = 500  # docs: 1-2500 chars per message, <500 recommended
AUDIO_QUEUE_SIZE = 600
KEEPALIVE_SECONDS = 20  # server closes idle connections after about a minute


class SarvamTTSError(RuntimeError):
    pass


def _is_open(ws: Any) -> bool:
    if ws is None:
        return False
    state = getattr(ws, "state", None)
    if state is not None:
        return getattr(state, "name", "") == "OPEN"
    return bool(getattr(ws, "open", False))


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


@dataclass
class _Context:
    voice_id: str
    audio_queue: asyncio.Queue = field(
        default_factory=lambda: asyncio.Queue(maxsize=AUDIO_QUEUE_SIZE)
    )
    chars: int = 0
    flushes_sent: int = 0
    finals_seen: int = 0
    closed: bool = False
    abandoned: bool = False
    first_audio_logged: bool = False
    started_at: float = 0.0


class SarvamTTS:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "bulbul:v4-flash",
        voice_id: str = "simran_en_customer",
        language: str = "en-IN",
        pace: float = 1.0,
        sample_rate: int = 8000,
        codec: str = "mulaw",
        min_buffer_size: int = 30,
        max_chunk_length: int = 150,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        if int(sample_rate) != 8000 or codec.lower() != "mulaw":
            raise ValueError("SarvamTTS only supports 8 kHz mulaw telephony output")
        self.api_key = api_key
        self.model = model
        self.voice_id = voice_id
        self.language = language
        self.pace = pace
        self.sample_rate = 8000
        self.codec = "mulaw"
        self.min_buffer_size = min_buffer_size
        self.max_chunk_length = max_chunk_length
        self.on_event = on_event

        self._ws: Any = None
        self._reader: asyncio.Task | None = None
        self._keepalive: asyncio.Task | None = None
        self._reconnect: asyncio.Task | None = None
        self._connect_lock = asyncio.Lock()
        self._configured_voice: str | None = None
        self._context_id: str | None = None
        self._context: _Context | None = None
        self._chars: dict[str, int] = {}
        self._closing = False

    # ------------------------------------------------------------------ API
    async def ensure_connected(self) -> None:
        if not self.api_key:
            raise SarvamTTSError("SARVAM_API_KEY is not set")
        if self._closing:
            raise SarvamTTSError("Sarvam TTS client is closed")
        if _is_open(self._ws):
            return
        async with self._connect_lock:
            if _is_open(self._ws):
                return
            await self._connect()

    async def create_context(self, context_id: str, *, voice_id: str | None = None) -> None:
        await self.ensure_connected()
        if self._context is not None:
            await self.abandon_context(self._context_id or "")
            await self.ensure_connected()
        voice = voice_id or self.voice_id
        context = _Context(voice_id=voice, started_at=asyncio.get_running_loop().time())
        self._context_id = context_id
        self._context = context
        self._chars[context_id] = 0
        if voice != self._configured_voice:
            await self._send_config(voice)
        log.info(
            "sarvam_tts_context_created",
            extra={"context_id": context_id, "voice_id": voice, "model": self.model},
        )

    async def send_text(self, context_id: str, text: str) -> None:
        # Sarvam answers punctuation/whitespace-only text with HTTP 400.
        if not text or not any(ch.isalnum() for ch in text):
            return
        context = self._require(context_id)
        if context.abandoned:
            return
        context.chars += len(text)
        self._chars[context_id] = context.chars
        # Flush per call: the agent already sends sentence-sized pieces, so
        # waiting for min_buffer_size would only add latency.  Sarvam emits one
        # "final" event per flush, which is how the end of the turn is known.
        for start in range(0, len(text), MAX_TEXT_CHUNK):
            piece = text[start : start + MAX_TEXT_CHUNK]
            await self._send({"type": "text", "data": {"text": piece}})
            await self._send({"type": "flush"})
            context.flushes_sent += 1

    async def close_context(self, context_id: str) -> None:
        context = self._context if self._context_id == context_id else None
        if context is None or context.closed:
            return
        context.closed = True
        self._maybe_finish(context)

    async def audio(self, context_id: str):
        """Yield μ-law bytes until every flushed segment has completed."""
        context = self._require(context_id)
        try:
            while True:
                item = await context.audio_queue.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                if context.abandoned:
                    continue
                yield item
        finally:
            if self._context is context:
                self._context = None
                self._context_id = None

    async def abandon_context(self, context_id: str) -> None:
        """Barge-in: Sarvam has no cancel message, so drop the connection."""
        context = self._context if self._context_id == context_id else None
        if context is None:
            return
        context.abandoned = True
        self._context = None
        self._context_id = None
        _put_nowait(context.audio_queue, None)
        log.debug("sarvam_tts_context_abandoned", extra={"context_id": context_id})
        if context.flushes_sent > context.finals_seen:
            # Synthesis is still queued server-side; only a new socket stops it.
            await self._drop_connection()
            if not self._closing:
                self._reconnect = asyncio.create_task(
                    self._background_reconnect(), name="sarvam-tts-reconnect"
                )

    def processed_characters(self, context_id: str) -> int:
        return self._chars.get(context_id, 0)

    async def aclose(self) -> None:
        self._closing = True
        if self._reconnect is not None:
            self._reconnect.cancel()
        if self._context is not None:
            self._context.abandoned = True
            _put_nowait(self._context.audio_queue, None)
            self._context = None
        await self._drop_connection()

    # -------------------------------------------------------------- internal
    def _require(self, context_id: str) -> _Context:
        if self._context is None or self._context_id != context_id:
            raise SarvamTTSError(f"Unknown TTS context {context_id}")
        return self._context

    def _url(self) -> str:
        query = urlencode({"model": self.model, "send_completion_event": "true"})
        return f"{WS_BASE_URL}?{query}"

    async def _connect(self) -> None:
        try:
            self._ws = await websockets.connect(
                self._url(),
                additional_headers={"Api-Subscription-Key": self.api_key},
                open_timeout=10,
                ping_interval=20,
                ping_timeout=20,
                max_size=8 * 1024 * 1024,
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary
            raise SarvamTTSError(f"Sarvam TTS connect failed: {exc}") from exc
        self._configured_voice = None
        await self._send_config(self.voice_id)
        self._reader = asyncio.create_task(self._read_loop(self._ws), name="sarvam-tts-read")
        self._keepalive = asyncio.create_task(
            self._keepalive_loop(self._ws), name="sarvam-tts-keepalive"
        )
        log.info("sarvam_tts_connected", extra={"model": self.model})

    async def _send_config(self, voice_id: str) -> None:
        await self._send(
            {
                "type": "config",
                "data": {
                    "model": self.model,
                    "language_code": self.language,
                    "speaker": voice_id,
                    "pace": self.pace,
                    "speech_sample_rate": self.sample_rate,
                    "output_audio_codec": self.codec,
                    "min_buffer_size": self.min_buffer_size,
                    "max_chunk_length": self.max_chunk_length,
                },
            }
        )
        self._configured_voice = voice_id

    async def _send(self, message: dict[str, Any]) -> None:
        if not _is_open(self._ws):
            await self.ensure_connected()
        try:
            await self._ws.send(json.dumps(message))
        except Exception as exc:  # noqa: BLE001 - provider boundary
            raise SarvamTTSError(f"Sarvam TTS send failed: {exc}") from exc

    async def _read_loop(self, ws: Any) -> None:
        try:
            async for raw in ws:
                try:
                    message = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider boundary
            log.warning("sarvam_tts_read_failed", extra={"error": str(exc)})
        finally:
            if ws is self._ws:
                self._fail_context("Sarvam TTS connection closed")

    def _dispatch(self, message: dict[str, Any]) -> None:
        context = self._context
        kind = message.get("type")
        data = message.get("data") or {}
        if kind == "audio":
            if context is None or context.abandoned:
                return
            try:
                chunk = base64.b64decode(data.get("audio", ""))
            except (TypeError, ValueError):
                return
            if not chunk:
                return
            if not context.first_audio_logged:
                context.first_audio_logged = True
                log.info(
                    "sarvam_tts_first_audio",
                    extra={
                        "context_id": self._context_id,
                        "request_to_first_audio_ms": round(
                            (asyncio.get_running_loop().time() - context.started_at) * 1000, 1
                        ),
                    },
                )
            _put_nowait(context.audio_queue, chunk)
        elif kind == "event":
            if data.get("event_type") == "final" and context is not None:
                context.finals_seen += 1
                self._maybe_finish(context)
        elif kind == "error":
            detail = str(data.get("message") or message)[:300]
            log.warning("sarvam_tts_error", extra={"error": detail})
            self._fail_context(f"Sarvam TTS error: {detail}")
            asyncio.get_running_loop().create_task(self._emit_error(detail))

    def _maybe_finish(self, context: _Context) -> None:
        if context.closed and context.finals_seen >= context.flushes_sent:
            _put_nowait(context.audio_queue, None)

    def _fail_context(self, message: str) -> None:
        context = self._context
        if context is None or context.abandoned:
            return
        _put_nowait(context.audio_queue, SarvamTTSError(message))
        _put_nowait(context.audio_queue, None)

    async def _keepalive_loop(self, ws: Any) -> None:
        try:
            while _is_open(ws):
                await asyncio.sleep(KEEPALIVE_SECONDS)
                if _is_open(ws):
                    await ws.send(json.dumps({"type": "ping"}))
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def _drop_connection(self) -> None:
        ws, self._ws = self._ws, None
        self._configured_voice = None
        for task in (self._reader, self._keepalive):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
        self._reader = self._keepalive = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    async def _background_reconnect(self) -> None:
        try:
            await self.ensure_connected()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - next create_context retries
            log.warning("sarvam_tts_reconnect_failed", extra={"error": str(exc)})

    async def _emit_error(self, message: str) -> None:
        if self.on_event:
            try:
                await self.on_event({"type": "error", "stage": "tts", "message": message})
            except Exception:
                log.exception("sarvam_tts_event_handler_failed")
