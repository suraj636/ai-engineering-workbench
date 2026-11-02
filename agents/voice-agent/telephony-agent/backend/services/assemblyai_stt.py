"""AssemblyAI Universal Streaming (v3 WebSocket) client.

Documented endpoint: wss://streaming.assemblyai.com/v3/ws
- Auth: `Authorization: <api key>` (no Bearer prefix)
- Audio: binary frames of raw PCM16, 50–1000 ms each
- Events: Begin, SpeechStarted, Turn (partials + end_of_turn), Termination, Error

Official references
-------------------
Quickstart (SDK + raw WebSocket): https://www.assemblyai.com/docs/streaming/getting-started/transcribe-streaming-audio
WebSocket API reference: https://www.assemblyai.com/docs/streaming/api-spec/streaming-websocket
Turn detection: https://www.assemblyai.com/docs/streaming/turn-detection
Context carryover: https://www.assemblyai.com/docs/streaming/universal-3-5-pro/context-carryover

Notes that matter here
----------------------
- Streaming is billed on WebSocket session duration, so close the session as
  soon as the call ends (we send Terminate and await Termination).
- `SpeechStarted` is only emitted when the model produces a transcript; it is
  not a raw VAD signal. Every SpeechStarted is followed by a Turn.
- `mode` and `interruption_delay` are Universal-3.5 Pro Streaming features.
- `agent_context` is updated after each agent reply and is capped at 1750 chars.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

import websockets

log = logging.getLogger("assemblyai_stt")

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]

ENDPOINT = "wss://streaming.assemblyai.com/v3/ws"
# AssemblyAI requires 50–1000 ms of audio per binary frame.
FRAME_BYTES = 1600  # 50 ms of 16 kHz mono PCM16


class AssemblyAISTT:
    def __init__(
        self,
        api_key: str,
        *,
        sample_rate: int = 16000,
        speech_model: str = "universal-3-5-pro",
        mode: str = "",
        min_turn_silence: int | None = None,
        max_turn_silence: int | None = None,
        vad_threshold: float | None = None,
        interruption_delay: int | None = None,
        on_event: EventCallback | None = None,
    ) -> None:
        self.api_key = api_key
        self.sample_rate = sample_rate
        self.speech_model = speech_model
        self.mode = mode
        self.min_turn_silence = min_turn_silence
        self.max_turn_silence = max_turn_silence
        self.vad_threshold = vad_threshold
        self.interruption_delay = interruption_delay
        self.on_event = on_event or _noop

        self._ws: Any = None
        self._supervisor: asyncio.Task | None = None
        self._sender: asyncio.Task | None = None
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=300)
        self._connected = asyncio.Event()
        self._terminated = asyncio.Event()
        self._closing = False
        self._audio_seconds = 0.0
        self._audio_seconds_sent = 0.0
        self._session_seconds = 0.0
        self._backoff = 1.0

    # ------------------------------------------------------------------ API
    async def start(self) -> None:
        if self._supervisor and not self._supervisor.done():
            return
        self._closing = False
        self._supervisor = asyncio.create_task(self._supervise(), name="asr-supervisor")

    async def wait_connected(self, timeout: float = 10.0) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def send_audio(self, pcm16: bytes) -> None:
        """Queue caller audio; never blocks the Plivo read loop."""
        if self._closing or not pcm16:
            return
        try:
            self._queue.put_nowait(pcm16)
        except asyncio.QueueFull:
            # Real-time audio: drop the oldest frame rather than fall behind.
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(pcm16)
            except asyncio.QueueFull:
                pass

    async def update_agent_context(self, text: str) -> None:
        """Tell ASR what the agent just said (improves short-reply accuracy)."""
        ws = self._ws
        if not ws or not text:
            return
        try:
            await ws.send(
                json.dumps({"type": "UpdateConfiguration", "agent_context": text[:1750]})
            )
        except Exception:
            log.debug("asr_agent_context_failed")

    @property
    def audio_seconds_sent(self) -> float:
        return self._audio_seconds_sent

    @property
    def reported_audio_seconds(self) -> float:
        return self._audio_seconds

    @property
    def session_seconds(self) -> float:
        """Provider-reported WebSocket session duration (what AssemblyAI bills)."""
        return self._session_seconds

    async def close(self) -> None:
        self._closing = True
        ws = self._ws
        if ws is not None:
            try:
                await ws.send(json.dumps({"type": "Terminate"}))
                await asyncio.wait_for(self._terminated.wait(), timeout=1.5)
            except (asyncio.TimeoutError, Exception):
                pass
        if self._supervisor:
            self._supervisor.cancel()
            try:
                await self._supervisor
            except (asyncio.CancelledError, Exception):
                pass
        await self._teardown_socket()
        self._connected.clear()

    # -------------------------------------------------------------- internal
    async def _supervise(self) -> None:
        while not self._closing:
            try:
                await self._connect()
                self._connected.set()
                self._backoff = 1.0
                await self._read_loop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - provider boundary
                if not self._closing:
                    log.warning("asr_connection_error", extra={"error": str(exc)})
                    await self._emit(
                        {
                            "type": "error",
                            "stage": "asr",
                            "message": f"ASR connection error: {exc}",
                            "recoverable": True,
                        }
                    )
            finally:
                self._connected.clear()
                await self._teardown_socket()
            if self._closing:
                break
            await self._emit(
                {
                    "type": "reconnecting",
                    "stage": "asr",
                    "message": "Reconnecting to AssemblyAI…",
                }
            )
            await asyncio.sleep(self._backoff)
            self._backoff = min(self._backoff * 2, 8.0)

    async def _connect(self) -> None:
        params: dict[str, Any] = {
            "speech_model": self.speech_model,
            "encoding": "pcm_s16le",
            "sample_rate": self.sample_rate,
            "session_heartbeat": "true",
        }
        # `mode` and the turn-silence overrides are optional; only send the ones
        # the operator configured so model defaults stay in charge.
        if self.mode:
            params["mode"] = self.mode
        if self.min_turn_silence:
            params["min_turn_silence"] = self.min_turn_silence
        if self.max_turn_silence:
            params["max_turn_silence"] = self.max_turn_silence
        if self.vad_threshold is not None:
            params["vad_threshold"] = self.vad_threshold
        if self.interruption_delay is not None:
            params["interruption_delay"] = self.interruption_delay
        if self.speech_model.startswith("universal-3"):
            params["language_codes"] = json.dumps(["en"])

        url = f"{ENDPOINT}?{urlencode(params)}"
        log.info(
            "asr_connecting",
            extra={"model": self.speech_model, "sample_rate": self.sample_rate},
        )
        self._ws = await websockets.connect(
            url,
            additional_headers={"Authorization": self.api_key},
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            max_size=8 * 1024 * 1024,
        )
        self._terminated.clear()
        self._sender = asyncio.create_task(self._send_loop(), name="asr-sender")

    async def _send_loop(self) -> None:
        buffer = bytearray()
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                break
            buffer.extend(chunk)
            while len(buffer) >= FRAME_BYTES:
                payload = bytes(buffer[:FRAME_BYTES])
                del buffer[:FRAME_BYTES]
                ws = self._ws
                if ws is None:
                    buffer.clear()
                    return
                try:
                    await ws.send(payload)
                    self._audio_seconds_sent += len(payload) / (2 * self.sample_rate)
                except Exception:
                    buffer.clear()
                    return
        if buffer and self._ws is not None:
            try:
                await self._ws.send(bytes(buffer))
                self._audio_seconds_sent += len(buffer) / (2 * self.sample_rate)
            except Exception:
                pass

    async def _read_loop(self) -> None:
        assert self._ws is not None
        async for raw in self._ws:
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                continue
            event_type = data.get("type")
            if event_type == "Begin":
                log.info("asr_session_begin", extra={"session_id": data.get("id")})
                await self._emit(
                    {
                        "type": "begin",
                        "stage": "asr",
                        "session_id": data.get("id"),
                        "expires_at": data.get("expires_at"),
                    }
                )
            elif event_type == "SpeechStarted":
                await self._emit(
                    {
                        "type": "speech_started",
                        "timestamp": data.get("timestamp"),
                        "confidence": data.get("confidence"),
                    }
                )
            elif event_type == "Turn":
                await self._emit(
                    {
                        "type": "turn",
                        "transcript": data.get("transcript") or "",
                        "end_of_turn": bool(data.get("end_of_turn")),
                        "end_of_turn_confidence": data.get("end_of_turn_confidence"),
                        "turn_order": data.get("turn_order"),
                        "turn_is_formatted": data.get("turn_is_formatted"),
                        "words": data.get("words") or [],
                    }
                )
            elif event_type == "Termination":
                self._audio_seconds = float(data.get("audio_duration_seconds") or 0.0)
                self._session_seconds = float(
                    data.get("session_duration_seconds") or 0.0
                )
                log.info(
                    "asr_terminated",
                    extra={
                        "audio_seconds": self._audio_seconds,
                        "session_seconds": data.get("session_duration_seconds"),
                    },
                )
                await self._emit(
                    {
                        "type": "termination",
                        "audio_seconds": self._audio_seconds,
                        "session_seconds": data.get("session_duration_seconds"),
                    }
                )
                self._terminated.set()
            elif event_type == "Error":
                message = data.get("error") or data.get("message") or "Unknown ASR error"
                log.error("asr_error", extra={"error": message})
                await self._emit(
                    {"type": "error", "stage": "asr", "message": str(message), "recoverable": True}
                )
            elif event_type == "Heartbeat":
                log.debug("asr_heartbeat")

    async def _teardown_socket(self) -> None:
        if self._sender:
            self._sender.cancel()
            try:
                await self._sender
            except (asyncio.CancelledError, Exception):
                pass
            self._sender = None
        # Discard audio queued for the dead connection.
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    async def _emit(self, event: dict[str, Any]) -> None:
        try:
            await self.on_event(event)
        except Exception:
            log.exception("asr_event_handler_failed")


async def _noop(_: dict[str, Any]) -> None:
    return None
