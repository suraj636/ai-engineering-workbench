"""Inworld TTS 2 persistent WebSocket client.

Documented channel: wss://api.inworld.ai/tts/v1/voice:streamBidirectional
Auth header:       Authorization: Basic <base64 console key>
Protocol:          create context -> sendText (incremental) -> closeContext
Audio:             `audioConfig.audioEncoding` supports MULAW at 8 kHz, which is
                   exactly Plivo's telephony format (no transcoding needed).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, AsyncIterator, Awaitable, Callable

import websockets

log = logging.getLogger("inworld_tts")

WS_URL = "wss://api.inworld.ai/tts/v1/voice:streamBidirectional"
MAX_TEXT_CHUNK = 2000  # documented UTF-16 limit per sendText


class InworldTTSError(RuntimeError):
    pass


def _is_open(ws: Any) -> bool:
    if ws is None:
        return False
    state = getattr(ws, "state", None)
    if state is not None:
        return getattr(state, "name", "") == "OPEN"
    return bool(getattr(ws, "open", False))


class InworldTTS:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "inworld-tts-2",
        voice_id: str = "Ashley",
        encoding: str = "MULAW",
        sample_rate: int = 8000,
        speaking_rate: float = 1.0,
        delivery_mode: str = "BALANCED",
        flush_per_chunk: bool = True,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.voice_id = voice_id
        self.encoding = encoding.upper()
        self.sample_rate = sample_rate
        self.speaking_rate = speaking_rate
        self.delivery_mode = delivery_mode
        self.flush_per_chunk = flush_per_chunk
        self.on_event = on_event

        self._ws: Any = None
        self._reader: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._closing = False
        self._contexts: dict[str, asyncio.Queue] = {}
        self._abandoned: set[str] = set()
        self._chars: dict[str, int] = {}
        self._drain_tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ API
    async def ensure_connected(self) -> None:
        if _is_open(self._ws):
            return
        async with self._lock:
            if _is_open(self._ws):
                return
            log.info("inworld_connecting", extra={"model": self.model})
            self._ws = await websockets.connect(
                WS_URL,
                additional_headers={"Authorization": f"Basic {self.api_key}"},
                open_timeout=10,
                ping_interval=20,
                ping_timeout=20,
                max_size=16 * 1024 * 1024,
            )
            self._reader = asyncio.create_task(self._read_loop(self._ws), name="inworld-reader")

    async def create_context(
        self,
        context_id: str,
        *,
        voice_id: str | None = None,
    ) -> None:
        await self.ensure_connected()
        self._contexts[context_id] = asyncio.Queue(maxsize=600)
        self._abandoned.discard(context_id)
        create: dict[str, Any] = {
            "voiceId": voice_id or self.voice_id,
            "modelId": self.model,
            "audioConfig": {
                "audioEncoding": self.encoding,
                "sampleRateHertz": self.sample_rate,
                "speakingRate": self.speaking_rate,
            },
            "applyTextNormalization": "ON",
        }
        if not self.flush_per_chunk:
            # Only when flush_per_chunk is off: server-controlled auto mode, where
            # Inworld holds text until a sentence boundary (the slower option).
            create["autoMode"] = True
            create["autoModeStrategy"] = "SENTENCE_BOUNDARY"
        if self.delivery_mode:
            create["deliveryMode"] = self.delivery_mode
        await self._send({"contextId": context_id, "create": create})
        log.info("inworld_context_created", extra={"context_id": context_id})

    async def send_text(self, context_id: str, text: str) -> None:
        if not text:
            return
        for start in range(0, len(text), MAX_TEXT_CHUNK):
            send: dict[str, Any] = {"text": text[start : start + MAX_TEXT_CHUNK]}
            if self.flush_per_chunk:
                # Manual mode: speak this chunk now instead of waiting for a
                # sentence end (the same idea as Sarvam's text + flush).
                send["flushContext"] = {}
            await self._send({"contextId": context_id, "sendText": send})

    async def close_context(self, context_id: str) -> None:
        try:
            await self._send({"contextId": context_id, "closeContext": {}})
        except Exception as exc:  # noqa: BLE001 - provider boundary
            log.warning(
                "inworld_close_context_failed",
                extra={"context_id": context_id, "error": str(exc)},
            )
            self._finish_context(context_id, InworldTTSError(str(exc)))

    async def audio(self, context_id: str) -> AsyncIterator[bytes]:
        """Yield μ-law/PCM bytes until Inworld confirms `contextClosed`."""
        queue = self._contexts.get(context_id)
        if queue is None:
            raise InworldTTSError(f"Unknown TTS context {context_id}")
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, InworldTTSError):
                    raise item
                if context_id in self._abandoned:
                    continue
                yield item
        finally:
            self._contexts.pop(context_id, None)
            self._abandoned.discard(context_id)

    async def abandon_context(self, context_id: str) -> None:
        """Barge-in: stop consuming audio and free the server-side context."""
        self._abandoned.add(context_id)
        queue = self._contexts.get(context_id)
        if queue is not None:
            task = asyncio.create_task(self._drain(context_id), name="inworld-drain")
            self._drain_tasks.add(task)
            task.add_done_callback(self._drain_tasks.discard)
        if _is_open(self._ws):
            try:
                await self._send({"contextId": context_id, "closeContext": {}})
            except Exception:
                self._finish_context(context_id, None)

    def processed_characters(self, context_id: str) -> int:
        return self._chars.get(context_id, 0)

    async def aclose(self) -> None:
        self._closing = True
        if self._reader:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):
                pass
            self._reader = None
        for task in list(self._drain_tasks):
            task.cancel()
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        for context_id in list(self._contexts):
            self._finish_context(context_id, None)

    # -------------------------------------------------------------- internal
    async def _send(self, payload: dict[str, Any]) -> None:
        await self.ensure_connected()
        assert self._ws is not None
        await self._ws.send(json.dumps(payload))

    async def _read_loop(self, ws: Any) -> None:
        try:
            async for raw in ws:
                self._handle(raw)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider boundary
            if not self._closing:
                log.warning("inworld_socket_error", extra={"error": str(exc)})
                await self._error(f"TTS connection error: {exc}")
        finally:
            if self._ws is ws:
                self._ws = None
            if not self._closing:
                self._fail_all("TTS connection closed")

    def _handle(self, raw: str | bytes) -> None:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return

        top_error = data.get("error")
        if top_error:
            message = (
                top_error.get("message") if isinstance(top_error, dict) else str(top_error)
            )
            log.error("inworld_error", extra={"error": message})
            self._fail_all(message or "Inworld error")
            return

        result = data.get("result") or {}
        context_id = result.get("contextId")
        status = result.get("status") or {}
        if status.get("code") not in (0, None):
            message = status.get("message") or "Inworld synthesis error"
            log.error(
                "inworld_status_error",
                extra={"context_id": context_id, "code": status.get("code"), "error": message},
            )
            if context_id:
                self._finish_context(context_id, InworldTTSError(message))
            return

        if "contextCreated" in result:
            log.info("inworld_context_ready", extra={"context_id": context_id})
            return

        if "audioChunk" in result:
            chunk = result.get("audioChunk") or {}
            usage = chunk.get("usage") or {}
            count = usage.get("processedCharactersCount")
            if context_id and isinstance(count, int):
                self._chars[context_id] = max(self._chars.get(context_id, 0), count)
            content = chunk.get("audioContent")
            if content and context_id:
                try:
                    self._put(context_id, base64.b64decode(content))
                except Exception:
                    log.exception("inworld_bad_audio_chunk")
            return

        if "flushCompleted" in result:
            log.debug("inworld_flush_completed", extra={"context_id": context_id})
            return

        if "contextClosed" in result:
            log.info("inworld_context_closed", extra={"context_id": context_id})
            self._finish_context(context_id, None)

    def _put(self, context_id: str, item: Any) -> None:
        queue = self._contexts.get(context_id)
        if queue is None:
            return
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

    def _finish_context(self, context_id: str, error: Exception | None) -> None:
        self._put(context_id, error if error is not None else None)

    def _fail_all(self, message: str) -> None:
        error = InworldTTSError(message)
        for context_id in list(self._contexts):
            self._finish_context(context_id, error)

    async def _drain(self, context_id: str) -> None:
        queue = self._contexts.get(context_id)
        if queue is None:
            return
        try:
            while True:
                item = await asyncio.wait_for(queue.get(), timeout=30)
                if item is None or isinstance(item, InworldTTSError):
                    break
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        finally:
            self._contexts.pop(context_id, None)
            self._abandoned.discard(context_id)

    async def _error(self, message: str) -> None:
        if self.on_event:
            try:
                await self.on_event({"type": "error", "stage": "tts", "message": message})
            except Exception:
                log.exception("inworld_event_handler_failed")
