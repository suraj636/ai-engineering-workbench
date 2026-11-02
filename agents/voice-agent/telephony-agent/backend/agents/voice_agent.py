"""Per-call voice agent: STT -> LLM -> TTS orchestration, barge-in and metrics.

The agent is transport-agnostic.  A `PlivoTransport` or a `BrowserTransport`
feeds it 8 kHz μ-law audio and receives 8 kHz μ-law audio back, so both call
types run the exact same pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Protocol

from backend import call_db
from backend.config import RuntimeConfig, Settings, render_greeting
from backend.services.assemblyai_stt import AssemblyAISTT
from backend.services.sarvam_llm import SarvamLLM
from backend.services.tts_factory import build_tts
from backend.session_store import (
    CallRecord,
    TurnMetrics,
    event_bus,
    registry,
    utc_now_iso,
)
from backend.utils.audio_utils import (
    StreamResampler,
    b64encode,
    mulaw_to_pcm16,
)

log = logging.getLogger("voice_agent")

LISTENING = "LISTENING"
THINKING = "THINKING"
SPEAKING = "SPEAKING"
INTERRUPTED = "INTERRUPTED"
ENDED = "ENDED"

# Small transport/queue cushion added when the first TTS chunk is queued. The
# byte-duration estimate alone can understate Plivo's actual playout, which
# would flip the agent to LISTENING while the audible tail is still playing.
# A playedStream checkpoint shortens this back to the real end when it arrives.
PLAYOUT_CUSHION_SECONDS = 0.2


def _is_speakable(text: str) -> bool:
    """True if the text has at least one letter or digit worth synthesizing."""
    return any(ch.isalnum() for ch in text)


class Transport(Protocol):
    async def send_audio(self, mulaw: bytes) -> None: ...
    async def clear_audio(self) -> None: ...
    async def resume_audio(self) -> None: ...
    async def checkpoint(self, name: str) -> None: ...
    async def close(self) -> None: ...


class PlivoTransport:
    """Sends `playAudio` / `clearAudio` / `checkpoint` JSON over the Plivo WS."""

    def __init__(self, websocket: Any, stream_id: str) -> None:
        self.websocket = websocket
        self.stream_id = stream_id
        self._lock = asyncio.Lock()
        self._closed = False
        self._muted = False

    async def send_audio(self, mulaw: bytes) -> None:
        if not mulaw or self._muted:
            return
        await self._send(
            {
                "event": "playAudio",
                "media": {
                    "contentType": "audio/x-mulaw",
                    "sampleRate": 8000,
                    "payload": b64encode(mulaw),
                },
            }
        )

    async def clear_audio(self) -> None:
        # Mute first so a chunk racing in from an in-flight TTS send cannot be
        # queued after clearAudio and leak a syllable of the old response.
        self._muted = True
        await self._send({"event": "clearAudio", "streamId": self.stream_id})

    async def resume_audio(self) -> None:
        self._muted = False

    async def checkpoint(self, name: str) -> None:
        await self._send(
            {"event": "checkpoint", "streamId": self.stream_id, "name": name}
        )

    async def close(self) -> None:
        self._closed = True

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._closed:
            return
        async with self._lock:
            try:
                await self.websocket.send_text(json.dumps(payload))
            except Exception:
                self._closed = True
                raise


class BrowserTransport:
    """Sends JSON audio frames to the browser test-call WebSocket."""

    def __init__(self, websocket: Any) -> None:
        self.websocket = websocket
        self._lock = asyncio.Lock()
        self._closed = False
        self._muted = False

    async def send_audio(self, mulaw: bytes) -> None:
        if not mulaw or self._muted:
            return
        await self._send(
            {
                "type": "audio",
                "encoding": "mulaw",
                "sampleRate": 8000,
                "payload": b64encode(mulaw),
            }
        )

    async def clear_audio(self) -> None:
        self._muted = True
        await self._send({"type": "clear"})

    async def resume_audio(self) -> None:
        self._muted = False

    async def checkpoint(self, name: str) -> None:
        return None

    async def close(self) -> None:
        self._closed = True
        try:
            await self.websocket.send_text(json.dumps({"type": "ended"}))
        except Exception:
            pass

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._closed:
            return
        async with self._lock:
            try:
                await self.websocket.send_text(json.dumps(payload))
            except Exception:
                self._closed = True
                raise


class VoiceAgent:
    def __init__(
        self,
        *,
        call_id: str,
        direction: str,
        transport: Transport,
        record: CallRecord,
        config: RuntimeConfig,
        settings: Settings,
        from_number: str = "",
        to_number: str = "",
    ) -> None:
        self.call_id = call_id
        self.direction = direction
        self.transport = transport
        self.record = record
        self.config = config
        self.settings = settings
        self.from_number = from_number
        self.to_number = to_number
        self.record.asr_model = config.asr_speech_model
        self.record.llm_model = config.sarvam_model

        self.state = "INITIALIZING"
        self.history: list[dict[str, str]] = [
            {"role": "system", "content": config.system_prompt}
        ]
        self._turn_index = 0
        self._current_turn: TurnMetrics | None = None
        self._turn_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._closing = False
        self._end_lock = asyncio.Lock()
        self._last_activity = time.time()
        self._idle_reprompted = False
        self._turn_spoken: list[str] = []
        self._active_tts_context: str | None = None
        self._tts_audio_seconds = 0.0
        self._playout: dict[str, float] = {}
        self._playout_end_at = 0.0
        self._last_checkpoint = ""
        self._asr_audio_started_at: float | None = None

        self._resampler = StreamResampler(8000, settings.assemblyai_sample_rate)
        self._stt = AssemblyAISTT(
            settings.assemblyai_api_key,
            sample_rate=settings.assemblyai_sample_rate,
            speech_model=config.asr_speech_model,
            mode=settings.assemblyai_mode,
            min_turn_silence=settings.assemblyai_min_turn_silence,
            max_turn_silence=settings.assemblyai_max_turn_silence,
            vad_threshold=settings.assemblyai_vad_threshold,
            interruption_delay=settings.assemblyai_interruption_delay,
            on_event=self._on_asr_event,
        )
        self._tts = build_tts(settings, config, on_event=self._on_tts_event)
        self._llm = SarvamLLM(
            settings.sarvam_api_key,
            base_url=settings.sarvam_base_url,
            model=config.sarvam_model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        missing = []
        if not self.settings.assemblyai_api_key:
            missing.append("ASSEMBLYAI_API_KEY")
        if not self.settings.sarvam_api_key:
            missing.append("SARVAM_API_KEY")
        if not self.settings.tts_configured_for(self.config.tts_provider):
            key_name = self.settings.tts_key_name(self.config.tts_provider)
            if key_name not in missing:
                missing.append(key_name)
        if missing:
            raise RuntimeError("Missing credentials: " + ", ".join(missing))

        self._publish(
            "call_started",
            direction=self.direction,
            from_number=self.from_number,
            to_number=self.to_number,
            tts_provider=self.config.tts_provider,
            tts_model=self.config.tts_model,
            voice_id=self.config.voice_id,
            sarvam_model=self.config.sarvam_model,
        )
        log.info(
            "call_started",
            extra={
                "call_id": self.call_id,
                "direction": self.direction,
                "from": self.from_number,
                "to": self.to_number,
            },
        )

        await self._stt.start()
        connected = await self._stt.wait_connected(timeout=10)
        if not connected:
            log.warning("asr_not_connected_yet", extra={"call_id": self.call_id})

        try:
            await self._tts.ensure_connected()
        except Exception as exc:  # noqa: BLE001 - provider boundary
            self.record.add_error(f"TTS connect failed: {exc}")
            log.warning("tts_preconnect_failed", extra={"call_id": self.call_id, "error": str(exc)})

        self._set_state(LISTENING)
        self._watchdog_task = asyncio.create_task(self._watchdog(), name="call-watchdog")
        if self.config.greeting:
            self._turn_task = asyncio.create_task(
                self._run_greeting(), name="agent-greeting"
            )

    async def send_caller_audio(self, mulaw: bytes) -> None:
        if self._closing or not mulaw:
            return
        if self._asr_audio_started_at is None:
            self._asr_audio_started_at = time.time()
        pcm16_8k = mulaw_to_pcm16(mulaw)
        pcm16_16k = self._resampler.process(pcm16_8k)
        self._stt.send_audio(pcm16_16k)

    async def on_played_stream(self, name: str) -> None:
        started = self._playout.pop(name, None)
        if started:
            self._publish(
                "playout_complete",
                name=name,
                playout_seconds=round(time.time() - started, 2),
            )
        # Plivo fires this after the audio queued before the checkpoint has been
        # played. It is the authoritative end-of-playout signal when it arrives;
        # the byte-duration estimate remains the fallback.
        if name and name == self._last_checkpoint:
            self._playout_end_at = min(self._playout_end_at, time.time())

    async def end(self, reason: str = "ended", *, hangup: bool = False) -> None:
        async with self._end_lock:
            if self._closing:
                return
            self._closing = True
            self.state = ENDED
            self._publish("call_state", state=ENDED)

            if self._turn_task and not self._turn_task.done():
                self._turn_task.cancel()
                try:
                    await self._turn_task
                except (asyncio.CancelledError, Exception):
                    pass
            if self._watchdog_task and not self._watchdog_task.done():
                self._watchdog_task.cancel()
                try:
                    await self._watchdog_task
                except (asyncio.CancelledError, Exception):
                    pass

            try:
                await self._tts.aclose()
            except Exception:
                pass
            try:
                await self._stt.close()
            except Exception:
                pass
            try:
                await self._llm.aclose()
            except Exception:
                pass

            if self._stt.reported_audio_seconds:
                self.record.asr_seconds = max(
                    self.record.asr_seconds, self._stt.reported_audio_seconds
                )
            self.record.asr_session_seconds = round(
                self._stt.session_seconds or self._stt.audio_seconds_sent, 2
            )
            self.record.finish(reason)
            registry.detach_agent(self.call_id)

            if hangup and self.direction in {"inbound", "outbound"}:
                from backend.services.plivo_service import plivo_service

                await plivo_service.hangup(self.record.call_id)

            try:
                await self.transport.close()
            except Exception:
                pass

            summary = self.record.to_dict(self.settings)
            self._save_history(summary)
            self._publish(
                "call_ended",
                reason=reason,
                duration_seconds=summary["duration_seconds"],
                costs=summary["costs"],
                usage={
                    "asr_seconds": summary["asr_seconds"],
                    "asr_session_seconds": summary["asr_session_seconds"],
                    "llm_prompt_tokens": summary["llm_prompt_tokens"],
                    "llm_cached_tokens": summary["llm_cached_tokens"],
                    "llm_completion_tokens": summary["llm_completion_tokens"],
                    "llm_output_characters": summary["llm_output_characters"],
                    "tts_characters": summary["tts_characters"],
                    "tts_audio_seconds": summary["tts_audio_seconds"],
                    "duration_seconds": summary["duration_seconds"],
                },
                avg_response_ms=summary["avg_response_ms"],
            )
            log.info(
                "call_ended",
                extra={
                    "call_id": self.call_id,
                    "reason": reason,
                    "duration_seconds": summary["duration_seconds"],
                    "turns": len(self.record.turns),
                    "avg_response_ms": summary["avg_response_ms"],
                    "tts_characters": summary["tts_characters"],
                    "cost_usd": summary["costs"].get("total"),
                },
            )

    def _save_history(self, summary: dict[str, Any]) -> None:
        """Write the finished call to SQLite off the event loop; never raises."""
        if not call_db.enabled():
            return
        try:
            task = asyncio.create_task(
                asyncio.to_thread(
                    call_db.save_call,
                    summary,
                    asr_model=self.record.asr_model,
                    llm_model=self.record.llm_model,
                    started_ts=self.record.started_at,
                ),
                name="call-history-save",
            )
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
        except Exception:  # noqa: BLE001 - history is best effort
            log.warning("call_history_schedule_failed", extra={"call_id": self.call_id})

    # --------------------------------------------------------------- ASR events
    async def _on_asr_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "begin":
            self._asr_audio_started_at = None
            return

        if kind == "speech_started":
            self._last_activity = time.time()
            self._idle_reprompted = False
            will_interrupt = self._should_barge_in()
            self._publish(
                "speech_started",
                state=self.state,
                confidence=event.get("confidence"),
                asr_timestamp_ms=event.get("timestamp"),
                interrupted=will_interrupt,
                playout_remaining_ms=round(self._pending_playout_seconds() * 1000, 1),
            )
            log.debug(
                "asr_speech_started",
                extra={
                    "call_id": self.call_id,
                    "state": self.state,
                    "will_interrupt": will_interrupt,
                },
            )
            if will_interrupt:
                await self._barge_in()
            return

        if kind == "turn":
            text = (event.get("transcript") or "").strip()
            if not text:
                return
            raw_turn = event.get("turn_order")
            turn_index = (
                int(raw_turn) if raw_turn is not None else self._turn_index
            )
            final = bool(event.get("end_of_turn"))
            self._last_activity = time.time()
            self._publish(
                "transcript",
                role="user",
                text=text,
                final=final,
                turn=turn_index,
                confidence=event.get("end_of_turn_confidence"),
            )
            if final:
                asr_delay = self._asr_endpoint_delay_ms(event.get("words") or [])
                await self._start_response_turn(text, asr_delay)
            return

        if kind == "termination":
            seconds = float(event.get("audio_seconds") or 0.0)
            if seconds:
                self.record.asr_seconds = max(self.record.asr_seconds, seconds)
            self._publish(
                "asr_termination",
                audio_seconds=seconds,
                session_seconds=event.get("session_seconds"),
            )
            return

        if kind == "error":
            message = str(event.get("message") or "ASR error")
            self.record.add_error(message)
            self._publish("error", stage=event.get("stage", "asr"), message=message)
            return

        if kind == "reconnecting":
            self._publish("provider_status", stage="asr", status="reconnecting", message=event.get("message"))

    async def _on_tts_event(self, event: dict[str, Any]) -> None:
        if event.get("type") == "error":
            self.record.add_error(str(event.get("message")))
            self._publish("error", stage="tts", message=event.get("message"))

    # ------------------------------------------------------------------ turns
    async def _start_response_turn(
        self, text: str, asr_delay_ms: float | None = None
    ) -> None:
        if self._closing:
            return
        # A final transcript without a preceding SpeechStarted (e.g. reconnect)
        # should still interrupt anything that is playing, including the tail of
        # a response that is still draining at Plivo.
        if self._should_barge_in():
            await self._barge_in()
        old_task = self._turn_task
        if old_task and not old_task.done() and old_task is not asyncio.current_task():
            old_task.cancel()
            await asyncio.gather(old_task, return_exceptions=True)

        self._turn_index += 1
        turn = TurnMetrics(index=self._turn_index, turn_id=f"t{self._turn_index}")
        turn.user_text = text
        turn.asr_final_delay_ms = asr_delay_ms
        self.record.turns.append(turn)
        self._publish(
            "turn_started",
            turn=turn.index,
            kind=turn.kind,
            user_text=text,
            asr_final_delay_ms=asr_delay_ms,
        )
        self._turn_task = asyncio.create_task(
            self._run_response_turn(turn, text), name=f"turn-{turn.index}"
        )

    def _asr_endpoint_delay_ms(self, words: list[dict[str, Any]]) -> float | None:
        """Wall-clock delay between the last word ending and the final Turn.

        AssemblyAI word timings are milliseconds from the start of the audio
        stream, so this is a real end-of-turn latency, not a silence guess.
        """
        if not words or self._asr_audio_started_at is None:
            return None
        ends = [w.get("end") for w in words if isinstance(w.get("end"), (int, float))]
        if not ends:
            return None
        elapsed_ms = (time.time() - self._asr_audio_started_at) * 1000
        return round(max(0.0, elapsed_ms - max(ends)), 1)

    async def _run_response_turn(self, turn: TurnMetrics, text: str) -> None:
        self._current_turn = turn
        self._turn_spoken = []
        self._idle_reprompted = False
        self.history.append({"role": "user", "content": text})
        self._set_state(THINKING)
        t0 = asyncio.get_running_loop().time()
        ctx = f"{self.call_id[-8:]}-t{turn.index}"
        self._active_tts_context = ctx

        try:
            await self._tts.create_context(ctx, voice_id=self.config.voice_id)
            await self._speak_llm_response(turn, ctx, t0)
            self._finalize_turn(turn, ctx, interrupted=False)
        except asyncio.CancelledError:
            turn.interrupted = True
            await self._abandon_tts(ctx)
            self._finalize_turn(turn, ctx, interrupted=True)
            raise
        except Exception as exc:  # noqa: BLE001 - provider boundary
            self.record.add_error(f"turn {turn.index}: {exc}")
            log.exception("turn_failed", extra={"call_id": self.call_id, "turn": turn.index})
            self._publish("error", stage="agent", message=f"Response failed: {exc}")
            await self._abandon_tts(ctx)
            self._finalize_turn(turn, ctx, interrupted=True)
            if not self._closing:
                await self._schedule_static(
                    "Sorry, I had a little trouble there. Could you say that again?"
                )
        finally:
            self._current_turn = None
            self._active_tts_context = None
            if (
                not self._closing
                and self._turn_task is asyncio.current_task()
                and self.state in {THINKING, SPEAKING}
            ):
                try:
                    await self._wait_for_playout()
                except asyncio.CancelledError:
                    pass
                if not self._closing:
                    self._set_state(LISTENING)
                    self._last_activity = time.time()

    async def _speak_llm_response(
        self, turn: TurnMetrics, ctx: str, t0: float
    ) -> None:
        loop = asyncio.get_running_loop()
        first_token_seen = False
        first_audio_seen = False
        usage_recorded = False

        def on_usage(usage: dict[str, Any]) -> None:
            nonlocal usage_recorded
            if usage_recorded:
                return
            usage_recorded = True
            turn.llm_prompt_tokens = int(usage.get("prompt_tokens") or 0)
            turn.llm_completion_tokens = int(usage.get("completion_tokens") or 0)
            details = usage.get("prompt_tokens_details") or {}
            turn.llm_cached_tokens = int(details.get("cached_tokens") or 0)

        async def generate() -> None:
            nonlocal first_token_seen
            buffer = ""
            async for token in self._llm.stream_chat(self.history, on_usage=on_usage):
                if self.state == INTERRUPTED or self._closing:
                    break
                if not first_token_seen:
                    first_token_seen = True
                    turn.llm_first_token_ms = (loop.time() - t0) * 1000
                    self._publish(
                        "llm_first_token",
                        turn=turn.index,
                        llm_first_token_ms=round(turn.llm_first_token_ms, 1),
                    )
                # The prompt forbids markdown, but a stray "**" would be read aloud.
                token = token.replace("*", "").replace("`", "")
                if not token:
                    continue
                buffer += token
                self._publish(
                    "assistant_delta", text=token, turn=turn.index, final=False
                )
                # Hold back text with nothing speakable (e.g. a lone "\n" or
                # "."): TTS providers reject it with a 400 and fail the turn.
                if _is_speakable(buffer) and (
                    len(buffer) >= 60 or buffer.endswith((".", "!", "?", ";", ":", "\n"))
                ):
                    self._turn_spoken.append(buffer)
                    await self._tts.send_text(ctx, buffer)
                    buffer = ""
            if _is_speakable(buffer) and self.state != INTERRUPTED and not self._closing:
                self._turn_spoken.append(buffer)
                await self._tts.send_text(ctx, buffer)
            if self.state != INTERRUPTED and not self._closing:
                await self._tts.close_context(ctx)

        async def play() -> None:
            nonlocal first_audio_seen
            checkpoint_name = ""
            # A previous barge-in muted the transport; this is the first audio
            # of a new response, so it is safe to unmute now.
            await self.transport.resume_audio()
            async for chunk in self._tts.audio(ctx):
                if self.state == INTERRUPTED or self._closing:
                    break
                now = loop.time()
                if not first_audio_seen:
                    first_audio_seen = True
                    turn.tts_first_audio_ms = (now - t0) * 1000
                    turn.first_audio_out_ms = (now - t0) * 1000
                    turn.total_ms = (now - t0) * 1000
                    checkpoint_name = f"turn-{turn.index}"
                    self._last_checkpoint = checkpoint_name
                    self._playout[checkpoint_name] = time.time()
                    self._playout_end_at = (
                        max(time.time(), self._playout_end_at) + PLAYOUT_CUSHION_SECONDS
                    )
                    self._publish(
                        "tts_first_audio",
                        turn=turn.index,
                        tts_first_audio_ms=round(turn.tts_first_audio_ms, 1),
                        tts_net_ttfa_ms=(
                            round(turn.tts_net_ttfa_ms, 1)
                            if turn.tts_net_ttfa_ms is not None
                            else None
                        ),
                    )
                    self._set_state(SPEAKING)
                    self._publish_latency(turn)
                await self.transport.send_audio(chunk)
                self._note_audio_queued(chunk)
                self._tts_audio_seconds += self._chunk_seconds(chunk)
            # Send the checkpoint only after the last chunk so playedStream
            # marks the true end of this turn's audio, not the first syllable.
            if checkpoint_name and self.state != INTERRUPTED and not self._closing:
                try:
                    await self.transport.checkpoint(checkpoint_name)
                except Exception:
                    pass

        generate_task = asyncio.create_task(generate(), name="llm-generate")
        play_task = asyncio.create_task(play(), name="tts-play")
        try:
            await asyncio.gather(generate_task, play_task)
        except asyncio.CancelledError:
            generate_task.cancel()
            play_task.cancel()
            await asyncio.gather(generate_task, play_task, return_exceptions=True)
            raise
        except Exception:
            generate_task.cancel()
            play_task.cancel()
            await asyncio.gather(generate_task, play_task, return_exceptions=True)
            raise

        if not self._turn_spoken:
            raise RuntimeError("LLM returned an empty response")

    async def _run_greeting(self) -> None:
        self._turn_index += 1
        turn = TurnMetrics(index=self._turn_index, turn_id=f"t{self._turn_index}", kind="greeting")
        greeting = render_greeting(self.config.greeting)
        turn.assistant_text = greeting
        self.record.turns.append(turn)
        self._current_turn = turn
        self._turn_spoken = [greeting]
        ctx = f"{self.call_id[-8:]}-greeting"
        self._active_tts_context = ctx
        t0 = asyncio.get_running_loop().time()

        try:
            await self._tts.create_context(ctx, voice_id=self.config.voice_id)
            await self._tts.send_text(ctx, greeting)
            await self._tts.close_context(ctx)
            await self._play_tts_only(turn, ctx, t0)
            self._finalize_turn(turn, ctx, interrupted=False)
        except asyncio.CancelledError:
            turn.interrupted = True
            await self._abandon_tts(ctx)
            self._finalize_turn(turn, ctx, interrupted=True)
            raise
        except Exception as exc:  # noqa: BLE001 - provider boundary
            self.record.add_error(f"greeting: {exc}")
            log.warning("greeting_failed", extra={"call_id": self.call_id, "error": str(exc)})
            await self._abandon_tts(ctx)
            self._finalize_turn(turn, ctx, interrupted=True)
        finally:
            self._current_turn = None
            self._active_tts_context = None
            if (
                not self._closing
                and self._turn_task is asyncio.current_task()
                and self.state in {THINKING, SPEAKING}
            ):
                try:
                    await self._wait_for_playout()
                except asyncio.CancelledError:
                    pass
                if not self._closing:
                    self._set_state(LISTENING)
                    self._last_activity = time.time()

    async def _speak_fallback(self, text: str) -> None:
        """Speak a canned line through the same TTS path (best effort)."""
        if self._closing:
            return
        self._turn_index += 1
        turn = TurnMetrics(index=self._turn_index, turn_id=f"t{self._turn_index}", kind="fallback")
        turn.assistant_text = text
        turn.user_text = ""
        self.record.turns.append(turn)
        self._current_turn = turn
        self._turn_spoken = [text]
        ctx = f"{self.call_id[-8:]}-fb{self._turn_index}"
        self._active_tts_context = ctx
        t0 = asyncio.get_running_loop().time()
        try:
            await self._tts.create_context(ctx, voice_id=self.config.voice_id)
            await self._tts.send_text(ctx, text)
            await self._tts.close_context(ctx)
            await self._play_tts_only(turn, ctx, t0)
            self._finalize_turn(turn, ctx, interrupted=False)
        except asyncio.CancelledError:
            turn.interrupted = True
            await self._abandon_tts(ctx)
            self._finalize_turn(turn, ctx, interrupted=True)
            raise
        except Exception as exc:  # noqa: BLE001 - provider boundary
            self.record.add_error(f"fallback tts: {exc}")
            await self._abandon_tts(ctx)
            self._finalize_turn(turn, ctx, interrupted=True)
        finally:
            self._current_turn = None
            self._active_tts_context = None
            if not self._closing and self._turn_task is asyncio.current_task():
                try:
                    await self._wait_for_playout()
                except asyncio.CancelledError:
                    pass
                if not self._closing:
                    self._set_state(LISTENING)
                    self._last_activity = time.time()

    async def _schedule_static(self, text: str) -> None:
        """Run a canned line as the current turn so barge-in can cancel it."""
        current = self._turn_task
        if current and not current.done() and current is not asyncio.current_task():
            current.cancel()
            await asyncio.gather(current, return_exceptions=True)
        self._turn_task = asyncio.create_task(self._speak_fallback(text), name="agent-static")

    async def _play_tts_only(self, turn: TurnMetrics, ctx: str, t0: float) -> None:
        loop = asyncio.get_running_loop()
        first = False
        checkpoint_name = ""
        await self.transport.resume_audio()
        async for chunk in self._tts.audio(ctx):
            if self.state == INTERRUPTED or self._closing:
                break
            now = loop.time()
            if not first:
                first = True
                turn.tts_first_audio_ms = (now - t0) * 1000
                turn.first_audio_out_ms = (now - t0) * 1000
                turn.total_ms = (now - t0) * 1000
                checkpoint_name = f"turn-{turn.index}"
                self._last_checkpoint = checkpoint_name
                self._playout[checkpoint_name] = time.time()
                self._playout_end_at = (
                    max(time.time(), self._playout_end_at) + PLAYOUT_CUSHION_SECONDS
                )
                self._publish(
                    "tts_first_audio",
                    turn=turn.index,
                    tts_first_audio_ms=round(turn.tts_first_audio_ms, 1),
                    tts_net_ttfa_ms=None,
                )
                self._set_state(SPEAKING)
                self._publish_latency(turn)
            await self.transport.send_audio(chunk)
            self._note_audio_queued(chunk)
            self._tts_audio_seconds += self._chunk_seconds(chunk)
        if checkpoint_name and self.state != INTERRUPTED and not self._closing:
            try:
                await self.transport.checkpoint(checkpoint_name)
            except Exception:
                pass

    async def _barge_in(self) -> None:
        if not self._should_barge_in():
            return
        pending_ms = round(self._pending_playout_seconds() * 1000, 1)
        previous_state = self.state
        turn_index = self._current_turn.index if self._current_turn else None
        log.info(
            "barge_in",
            extra={
                "call_id": self.call_id,
                "state": self.state,
                "playout_remaining_ms": pending_ms,
            },
        )
        if self._current_turn:
            self._current_turn.interrupted = True
        self._set_state(INTERRUPTED)
        # Mute and discard the local playout estimate before cancelling so the
        # turn's cleanup cannot queue more audio after clearAudio.
        self._reset_playout()
        task = self._turn_task
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        cleared = False
        try:
            await self.transport.clear_audio()
            cleared = True
        except Exception:
            log.warning("barge_in_clear_failed", extra={"call_id": self.call_id})
        self._publish(
            "barge_in",
            turn=turn_index,
            previous_state=previous_state,
            playout_remaining_ms=pending_ms,
            cleared=cleared,
        )
        if not self._closing:
            self._set_state(LISTENING)
            self._last_activity = time.time()

    def _should_barge_in(self) -> bool:
        if self._closing:
            return False
        if self.state in {THINKING, SPEAKING}:
            return True
        # Safety net: if a state transition raced ahead of the playout estimate,
        # callers should still be able to stop audio that is audibly playing.
        return self.state == LISTENING and self._pending_playout_seconds() > 0.05

    def _pending_playout_seconds(self) -> float:
        return max(0.0, self._playout_end_at - time.time())

    def _note_audio_queued(self, chunk: bytes) -> None:
        """Extend the estimated playout end by the duration just queued."""
        seconds = self._chunk_seconds(chunk)
        now = time.time()
        self._playout_end_at = max(now, self._playout_end_at) + seconds

    def _reset_playout(self) -> None:
        self._playout.clear()
        self._playout_end_at = 0.0
        self._last_checkpoint = ""

    async def _wait_for_playout(self) -> None:
        """Keep the turn in SPEAKING until Plivo has drained its audio buffer.

        Without this the agent flips to LISTENING as soon as the last chunk is
        queued, so a caller who interrupts the audible tail is ignored.
        """
        while not self._closing and self.state not in {INTERRUPTED, ENDED}:
            remaining = self._playout_end_at - time.time()
            if remaining <= 0.03:
                return
            await asyncio.sleep(min(remaining, 0.1))

    # ---------------------------------------------------------------- shutdown
    async def _watchdog(self) -> None:
        started = time.time()
        try:
            while not self._closing:
                await asyncio.sleep(2)
                now = time.time()
                if now - started > self.settings.max_call_seconds:
                    asyncio.create_task(self.end("max_duration", hangup=True))
                    return
                if self.state == LISTENING and now - self._last_activity > self.settings.idle_timeout_seconds:
                    if not self._idle_reprompted:
                        self._idle_reprompted = True
                        self._publish("idle_reprompt")
                        asyncio.create_task(
                            self._schedule_static(
                                "Are you still there? I can help whenever you're ready."
                            )
                        )
                    else:
                        asyncio.create_task(self.end("idle_timeout", hangup=True))
                        return
        except asyncio.CancelledError:
            raise

    # ------------------------------------------------------------------ helpers
    def _finalize_turn(self, turn: TurnMetrics, ctx: str, *, interrupted: bool) -> None:
        text = "".join(self._turn_spoken).strip()
        turn.assistant_text = text
        turn.interrupted = interrupted
        turn.llm_output_characters = len(text) if turn.kind == "response" else 0
        characters = self._tts.processed_characters(ctx)
        turn.tts_characters = characters or len(text)
        if not turn.counted:
            turn.counted = True
            self.record.llm_prompt_tokens += turn.llm_prompt_tokens
            self.record.llm_cached_tokens += turn.llm_cached_tokens
            self.record.llm_completion_tokens += turn.llm_completion_tokens
            self.record.llm_output_characters += turn.llm_output_characters
            self.record.tts_characters += turn.tts_characters
            self.record.tts_audio_seconds = round(self._tts_audio_seconds, 3)

        if text:
            self.history.append({"role": "assistant", "content": text})
            self._trim_history()
            if not interrupted and self.state != INTERRUPTED:
                self._publish("transcript", role="assistant", text=text, final=True, turn=turn.index)
            if not interrupted:
                asyncio.create_task(self._stt.update_agent_context(text))

        self._publish(
            "turn_complete",
            turn=turn.index,
            kind=turn.kind,
            interrupted=interrupted,
            latency=turn.to_dict(),
            usage={
                "llm_prompt_tokens": turn.llm_prompt_tokens,
                "llm_cached_tokens": turn.llm_cached_tokens,
                "llm_completion_tokens": turn.llm_completion_tokens,
                "llm_output_characters": turn.llm_output_characters,
                "tts_characters": turn.tts_characters,
            },
        )
        self._publish_usage()
        # Unmeasured stages (e.g. interrupted before any audio) are None, not 0.
        def measured(value: float | None) -> float | None:
            return round(value, 1) if value else None

        summary: dict[str, Any] = {
            "call_id": self.call_id,
            "turn": turn.index,
            "kind": turn.kind,
            "interrupted": interrupted,
            "asr_final_delay_ms": measured(turn.asr_final_delay_ms),
            "llm_first_token_ms": measured(turn.llm_first_token_ms),
            "tts_first_audio_ms": measured(turn.tts_first_audio_ms),
            "tts_net_ttfa_ms": measured(turn.tts_net_ttfa_ms),
            "total_ms": measured(turn.total_ms),
            "tts_characters": turn.tts_characters,
        }
        if self.settings.log_transcripts:
            summary["user"] = (turn.user_text or "")[:80]
            summary["agent"] = text[:80]
        log.info("turn_complete", extra=summary)

    def _trim_history(self) -> None:
        limit = max(4, self.settings.agent_max_history_messages)
        if len(self.history) <= limit:
            return
        system = self.history[0] if self.history[0]["role"] == "system" else None
        tail = self.history[-(limit - 1) :]
        self.history = ([system] if system else []) + tail

    def _chunk_seconds(self, chunk: bytes) -> float:
        if self.config.tts_provider in ("speechify", "sarvam"):
            return len(chunk) / 8000  # raw 8 kHz μ-law, one byte per sample
        if self.settings.inworld_audio_encoding == "MULAW":
            return len(chunk) / self.settings.inworld_sample_rate
        return len(chunk) / (2 * self.settings.inworld_sample_rate)

    async def _abandon_tts(self, ctx: str) -> None:
        try:
            await self._tts.abandon_context(ctx)
        except Exception:
            pass

    def _set_state(self, state: str) -> None:
        if self.state == state:
            return
        self.state = state
        self._publish("call_state", state=state)

    def _publish_latency(self, turn: TurnMetrics) -> None:
        self._publish(
            "latency",
            turn=turn.index,
            llm_first_token_ms=turn.llm_first_token_ms,
            tts_first_audio_ms=turn.tts_first_audio_ms,
            tts_net_ttfa_ms=turn.tts_net_ttfa_ms,
            first_audio_out_ms=turn.first_audio_out_ms,
            total_ms=turn.total_ms,
            asr_final_delay_ms=turn.asr_final_delay_ms,
        )

    def _publish_usage(self) -> None:
        self._publish(
            "usage",
            usage={
                "asr_seconds": round(self.record.asr_seconds, 2),
                "asr_session_seconds": round(self.record.asr_session_seconds, 2),
                "llm_prompt_tokens": self.record.llm_prompt_tokens,
                "llm_cached_tokens": self.record.llm_cached_tokens,
                "llm_completion_tokens": self.record.llm_completion_tokens,
                "llm_output_characters": self.record.llm_output_characters,
                "tts_characters": self.record.tts_characters,
                "tts_audio_seconds": round(self.record.tts_audio_seconds, 2),
                "duration_seconds": round(self.record.duration_seconds, 1),
            },
            costs=self.record.costs(self.settings),
        )

    def _publish(self, event_type: str, **payload: Any) -> None:
        event_bus.publish(
            {
                "type": event_type,
                "call_id": self.call_id,
                "direction": self.direction,
                "at": utc_now_iso(),
                **payload,
            }
        )
