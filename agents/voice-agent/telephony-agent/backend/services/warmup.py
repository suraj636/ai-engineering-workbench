"""Startup warm-up for every upstream provider.

The checks run in parallel as a background task so the HTTP server is ready
immediately. Each check opens a *real* provider session (REST auth for Plivo,
a streaming AssemblyAI session, a tiny Sarvam completion and a short TTS
synthesis for each configured provider) so DNS, TLS, auth and provider edge
caches are warm before the first caller arrives.

Results are exposed by ``/api/health`` and ``/api/warmup`` and published on
the dashboard event bus as ``warmup`` events. Failures are reported, never
raised: a provider outage must not stop the server from accepting calls.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from backend.config import RuntimeConfig, Settings, runtime_config, settings
from backend.services.assemblyai_stt import AssemblyAISTT
from backend.services.inworld_tts import InworldTTS
from backend.services.plivo_service import plivo_service
from backend.services.sarvam_llm import SarvamLLM
from backend.services.sarvam_tts import SarvamTTS
from backend.services.speechify_tts import SpeechifyTTS
from backend.session_store import event_bus, utc_now_iso

log = logging.getLogger("warmup")

PROVIDERS = ("plivo", "assemblyai", "sarvam", "inworld", "speechify", "sarvam_tts")
PROVIDER_LABELS = {
    "plivo": "Plivo",
    "assemblyai": "AssemblyAI",
    "sarvam": "Sarvam LLM",
    "inworld": "Inworld TTS",
    "speechify": "Speechify TTS",
    "sarvam_tts": "Sarvam TTS",
}
# TTS provider setting -> warm-up check that proves it works
TTS_WARMUP_KEY = {"inworld": "inworld", "speechify": "speechify", "sarvam": "sarvam_tts"}


def _server_port(default: int) -> int:
    """The uvicorn CLI ignores PORT in .env, so prefer an explicit --port."""
    args = sys.argv
    for index, arg in enumerate(args):
        if arg == "--port" and index + 1 < len(args) and args[index + 1].isdigit():
            return int(args[index + 1])
        if arg.startswith("--port=") and arg[7:].isdigit():
            return int(arg[7:])
    return default

PENDING = "pending"
RUNNING = "running"
COMPLETE = "complete"
WARMING = "warming"
READY = "ready"
ERROR = "error"
SKIPPED = "skipped"
DISABLED = "disabled"

WARMUP_TEXT = "Ready."


def _iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="milliseconds")


class ProviderWarmup:
    """Parallel, best-effort provider warm-up with a live status snapshot."""

    def __init__(
        self,
        app_settings: Settings | None = None,
        config: RuntimeConfig | None = None,
    ) -> None:
        self.settings = app_settings or settings
        self.config = config or runtime_config
        self._status = PENDING
        self._providers: dict[str, dict[str, Any]] = {
            name: {
                "status": PENDING,
                "latency_ms": None,
                "detail": "",
                "checked_at": None,
            }
            for name in PROVIDERS
        }
        self._started_at: float | None = None
        self._finished_at: float | None = None
        self._duration_ms: float | None = None
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------ API
    def start(self, *, force: bool = False) -> asyncio.Task | None:
        """Schedule the warm-up without blocking server startup."""
        if not self.settings.warmup_on_start and not force:
            self._mark_disabled()
            return None
        if self._task and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self._run(), name="provider-warmup")
        return self._task

    async def aclose(self) -> None:
        task, self._task = self._task, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    def snapshot(self) -> dict[str, Any]:
        providers = {name: dict(info) for name, info in self._providers.items()}
        ready = sum(1 for info in providers.values() if info["status"] == READY)
        errors = sum(1 for info in providers.values() if info["status"] == ERROR)
        return {
            "enabled": bool(self.settings.warmup_on_start),
            "status": self._status,
            "started_at": _iso(self._started_at),
            "finished_at": _iso(self._finished_at),
            "duration_ms": self._duration_ms,
            "timeout_seconds": self.settings.warmup_timeout_seconds,
            "providers": providers,
            "ready_count": ready,
            "error_count": errors,
        }

    # -------------------------------------------------------------- internal
    async def _run(self) -> None:
        self._status = RUNNING
        self._started_at = time.time()
        self._finished_at = None
        self._duration_ms = None
        self._providers = {
            name: {
                "status": PENDING,
                "latency_ms": None,
                "detail": "",
                "checked_at": None,
            }
            for name in PROVIDERS
        }

        checks: list[asyncio.Task] = []
        for name in PROVIDERS:
            missing = self._missing_credentials(name)
            if missing:
                self._set(
                    name,
                    SKIPPED,
                    detail=f"Skipped — missing {missing}",
                    publish=False,
                )
                continue
            self._set(name, WARMING, detail="Connecting…", publish=False)
            checks.append(
                asyncio.create_task(self._run_check(name), name=f"warmup-{name}")
            )

        self._publish(None, RUNNING)
        log.info(
            "warmup_started",
            extra={
                "timeout_seconds": self.settings.warmup_timeout_seconds,
                "checks": [task.get_name() for task in checks],
            },
        )

        if checks:
            await asyncio.gather(*checks, return_exceptions=True)

        self._status = COMPLETE
        self._finished_at = time.time()
        self._duration_ms = round((self._finished_at - self._started_at) * 1000, 1)
        summary = {
            name: {
                "status": info["status"],
                "latency_ms": info["latency_ms"],
            }
            for name, info in self._providers.items()
        }
        log.info(
            "warmup_complete",
            extra={
                "duration_ms": self._duration_ms,
                "ready_count": self.snapshot()["ready_count"],
                "error_count": self.snapshot()["error_count"],
                "providers": summary,
            },
        )
        self._publish(None, COMPLETE)
        self._log_readiness()

    def _log_readiness(self) -> None:
        """One clear line saying whether the dashboard is ready for test calls."""
        tts_key = TTS_WARMUP_KEY.get(self.config.tts_provider, self.config.tts_provider)
        web_needs = ("assemblyai", "sarvam", tts_key)

        def problems(names: tuple[str, ...]) -> list[str]:
            out = []
            for name in names:
                info = self._providers.get(name, {})
                if info.get("status") != READY:
                    detail = str(info.get("detail") or info.get("status") or "not ready")[:90]
                    out.append(f"{PROVIDER_LABELS.get(name, name)}: {detail}")
            return out

        web_blockers = problems(web_needs)
        phone_blockers = problems(("plivo",))
        if not self.settings.public_base_url:
            phone_blockers.append("PUBLIC_BASE_URL is not set")

        if not web_blockers and not phone_blockers:
            state = "ready"
        elif not web_blockers:
            state = "web_only"
        else:
            state = "not_ready"
        extra = {
            "state": state,
            "duration_ms": self._duration_ms,
            "tts": PROVIDER_LABELS.get(tts_key, tts_key),
            "dashboard": f"http://127.0.0.1:{_server_port(self.settings.port)}",
            "blockers": web_blockers or phone_blockers,
        }
        if state == "ready":
            log.info("server_ready", extra=extra)
        else:
            log.warning("server_ready", extra=extra)

    async def _run_check(self, name: str) -> None:
        checker = {
            "plivo": self._check_plivo,
            "assemblyai": self._check_assemblyai,
            "sarvam": self._check_sarvam,
            "inworld": self._check_inworld,
            "speechify": self._check_speechify,
            "sarvam_tts": self._check_sarvam_tts,
        }[name]
        timeout = max(1.0, float(self.settings.warmup_timeout_seconds))
        started = time.perf_counter()
        try:
            detail = await asyncio.wait_for(checker(), timeout=timeout)
            latency_ms = (time.perf_counter() - started) * 1000
            self._set(name, READY, latency_ms=latency_ms, detail=detail)
            log.info(
                "warmup_ready",
                extra={"provider": name, "latency_ms": round(latency_ms, 1), "detail": detail},
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            latency_ms = (time.perf_counter() - started) * 1000
            detail = f"Timed out after {timeout:.0f}s"
            self._set(name, ERROR, latency_ms=latency_ms, detail=detail)
            log.warning(
                "warmup_timeout",
                extra={"provider": name, "latency_ms": round(latency_ms, 1)},
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary
            latency_ms = (time.perf_counter() - started) * 1000
            detail = str(exc)[:300] or exc.__class__.__name__
            self._set(name, ERROR, latency_ms=latency_ms, detail=detail)
            log.warning(
                "warmup_failed",
                extra={
                    "provider": name,
                    "latency_ms": round(latency_ms, 1),
                    "error": detail,
                },
            )

    # ----------------------------------------------------------- provider checks
    async def _check_plivo(self) -> str:
        account = await plivo_service.check_account()
        label = account.get("name") or account.get("account_type") or "account"
        return f"REST auth ok · {label}"

    async def _check_assemblyai(self) -> str:
        begin = asyncio.Event()

        async def on_event(event: dict[str, Any]) -> None:
            if event.get("type") == "begin":
                begin.set()

        stt = AssemblyAISTT(
            self.settings.assemblyai_api_key,
            sample_rate=self.settings.assemblyai_sample_rate,
            speech_model=self.config.asr_speech_model,
            mode=self.settings.assemblyai_mode,
            min_turn_silence=self.settings.assemblyai_min_turn_silence,
            max_turn_silence=self.settings.assemblyai_max_turn_silence,
            vad_threshold=self.settings.assemblyai_vad_threshold,
            interruption_delay=self.settings.assemblyai_interruption_delay,
            on_event=on_event,
        )
        try:
            await stt.start()
            if not await stt.wait_connected(timeout=8):
                raise RuntimeError("AssemblyAI WebSocket connection timed out")
            try:
                await asyncio.wait_for(begin.wait(), timeout=8)
            except asyncio.TimeoutError as exc:
                raise RuntimeError("AssemblyAI connected but never sent Begin") from exc
            # Exercise the binary send path with 50 ms of silence.
            stt.send_audio(b"\x00" * 1600)
            await asyncio.sleep(0.15)
            return f"{self.config.asr_speech_model} · stream ready"
        finally:
            await stt.close()

    async def _check_sarvam(self) -> str:
        llm = SarvamLLM(
            self.settings.sarvam_api_key,
            base_url=self.settings.sarvam_base_url,
            model=self.config.sarvam_model,
            temperature=0.0,
            max_tokens=8,
        )
        started = time.perf_counter()
        first_token_ms: float | None = None
        try:
            messages = [
                {
                    "role": "user",
                    "content": "Reply with the single word: ready.",
                }
            ]
            async for token in llm.stream_chat(messages):
                if token.strip():
                    first_token_ms = (time.perf_counter() - started) * 1000
                    break
            if first_token_ms is None:
                raise RuntimeError("Sarvam returned no content tokens")
            return (
                f"{self.config.sarvam_model} · first token "
                f"{first_token_ms:.0f} ms"
            )
        finally:
            await llm.aclose()

    async def _check_inworld(self) -> str:
        tts = InworldTTS(
            self.settings.inworld_api_key,
            model=self.config.inworld_model,
            voice_id=self.config.voice_for("inworld"),
            encoding=self.settings.inworld_audio_encoding,
            sample_rate=self.settings.inworld_sample_rate,
            speaking_rate=self.config.speaking_rate,
            delivery_mode=self.config.delivery_mode,
            flush_per_chunk=self.settings.inworld_flush_per_chunk,
        )
        context_id = f"warmup-{uuid.uuid4().hex[:8]}"
        started = time.perf_counter()
        first_audio_ms: float | None = None
        total_bytes = 0
        try:
            await tts.create_context(context_id)
            await tts.send_text(context_id, WARMUP_TEXT)
            await tts.close_context(context_id)
            async for chunk in tts.audio(context_id):
                if not chunk:
                    continue
                if first_audio_ms is None:
                    first_audio_ms = (time.perf_counter() - started) * 1000
                total_bytes += len(chunk)
            if not total_bytes:
                raise RuntimeError("Inworld returned no audio")
            if first_audio_ms is None:
                raise RuntimeError("Inworld returned audio without a first chunk")
            return (
                f"{self.config.inworld_model} · {self.config.voice_for('inworld')} · "
                f"first audio {first_audio_ms:.0f} ms · {total_bytes} bytes"
            )
        finally:
            await tts.aclose()

    async def _check_speechify(self) -> str:
        tts = SpeechifyTTS(
            self.settings.speechify_api_key,
            base_url=self.settings.speechify_base_url,
            model=self.config.speechify_model,
            voice_id=self.config.voice_for("speechify"),
            language=self.settings.speechify_language,
            output_format=self.settings.speechify_output_format,
            sample_rate=8000,
        )
        context_id = f"warmup-{uuid.uuid4().hex[:8]}"
        started = time.perf_counter()
        first_audio_ms: float | None = None
        total_bytes = 0
        try:
            await tts.create_context(context_id)
            await tts.send_text(context_id, WARMUP_TEXT)
            await tts.close_context(context_id)
            async for chunk in tts.audio(context_id):
                if not chunk:
                    continue
                if first_audio_ms is None:
                    first_audio_ms = (time.perf_counter() - started) * 1000
                total_bytes += len(chunk)
            if not total_bytes:
                raise RuntimeError("Speechify returned no audio")
            if first_audio_ms is None:
                raise RuntimeError("Speechify returned audio without a first chunk")
            return (
                f"{self.config.speechify_model} · {self.config.voice_for('speechify')} · "
                f"first audio {first_audio_ms:.0f} ms · {total_bytes} bytes"
            )
        finally:
            await tts.aclose()

    async def _check_sarvam_tts(self) -> str:
        tts = SarvamTTS(
            self.settings.sarvam_api_key,
            model=self.config.sarvam_tts_model,
            voice_id=self.config.voice_for("sarvam"),
            language=self.settings.sarvam_tts_language,
            pace=self.settings.sarvam_tts_pace,
        )
        context_id = f"warmup-{uuid.uuid4().hex[:8]}"
        started = time.perf_counter()
        first_audio_ms: float | None = None
        total_bytes = 0
        try:
            await tts.create_context(context_id)
            await tts.send_text(context_id, WARMUP_TEXT)
            await tts.close_context(context_id)
            async for chunk in tts.audio(context_id):
                if not chunk:
                    continue
                if first_audio_ms is None:
                    first_audio_ms = (time.perf_counter() - started) * 1000
                total_bytes += len(chunk)
            if not total_bytes:
                raise RuntimeError("Sarvam TTS returned no audio")
            return (
                f"{self.config.sarvam_tts_model} · {self.config.voice_for('sarvam')} · "
                f"first audio {first_audio_ms:.0f} ms · {total_bytes} bytes"
            )
        finally:
            await tts.aclose()

    # ---------------------------------------------------------------- helpers
    def _missing_credentials(self, name: str) -> str:
        if name == "plivo":
            missing = []
            if not self.settings.plivo_auth_id:
                missing.append("PLIVO_AUTH_ID")
            if not self.settings.plivo_auth_token:
                missing.append("PLIVO_AUTH_TOKEN")
            if not self.settings.plivo_phone_number:
                missing.append("PLIVO_PHONE_NUMBER")
            return ", ".join(missing)
        if name == "assemblyai":
            return "" if self.settings.assemblyai_api_key else "ASSEMBLYAI_API_KEY"
        if name in ("sarvam", "sarvam_tts"):
            return "" if self.settings.sarvam_api_key else "SARVAM_API_KEY"
        if name == "inworld":
            return "" if self.settings.inworld_api_key else "INWORLD_API_KEY"
        if name == "speechify":
            return "" if self.settings.speechify_api_key else "SPEECHIFY_API_KEY"
        return ""

    def _mark_disabled(self) -> None:
        self._status = DISABLED
        self._providers = {
            name: {
                "status": DISABLED,
                "latency_ms": None,
                "detail": "Warm-up disabled (WARMUP_ON_START=false)",
                "checked_at": None,
            }
            for name in PROVIDERS
        }

    def _set(
        self,
        name: str,
        status: str,
        *,
        latency_ms: float | None = None,
        detail: str = "",
        publish: bool = True,
    ) -> None:
        self._providers[name] = {
            "status": status,
            "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
            "detail": detail,
            "checked_at": utc_now_iso() if status in {READY, ERROR, SKIPPED} else None,
        }
        if publish:
            self._publish(name, status)

    def _publish(self, provider: str | None, status: str) -> None:
        event_bus.publish(
            {
                "type": "warmup",
                "provider": provider,
                "status": status,
                "snapshot": self.snapshot(),
            }
        )


warmup_manager = ProviderWarmup()
