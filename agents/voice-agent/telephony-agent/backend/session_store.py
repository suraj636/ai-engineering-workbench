"""In-memory call registry, usage accounting, pricing and the UI event bus."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("session_store")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _cost(usage: float, rate: float | None, divisor: float = 1.0) -> float | None:
    if rate is None:
        return None
    return round((usage / divisor) * rate, 6)


def llm_cost(
    prompt_tokens: int,
    cached_tokens: int,
    completion_tokens: int,
    output_characters: int,
    rates: dict[str, Any],
) -> float | None:
    """Sarvam bills input, cached input and output tokens separately.

    ``prompt_tokens`` already includes the cached ones, so the uncached part is
    the difference. When the API reported no output tokens (older records, a
    failed usage event) output falls back to the character estimate.
    """
    input_rate = rates.get("llm_input_per_1m_tokens")
    cached_rate = rates.get("llm_cached_per_1m_tokens")
    output_rate = rates.get("llm_output_per_1m_tokens")
    if input_rate is None or cached_rate is None or output_rate is None:
        return None
    cached = min(max(cached_tokens, 0), max(prompt_tokens, 0))
    uncached = max(prompt_tokens, 0) - cached
    if completion_tokens > 0:
        output = completion_tokens * output_rate
    else:
        chars_rate = rates.get("llm_per_1m_generated_chars")
        if chars_rate is None:
            return None
        output = output_characters * chars_rate
    return round((uncached * input_rate + cached * cached_rate + output) / 1_000_000.0, 6)


@dataclass
class TurnMetrics:
    index: int
    turn_id: str
    kind: str = "response"  # response | greeting | fallback
    counted: bool = False
    user_text: str = ""
    assistant_text: str = ""
    started_at: float = field(default_factory=time.time)
    asr_final_delay_ms: float | None = None
    llm_first_token_ms: float | None = None
    tts_first_audio_ms: float | None = None
    first_audio_out_ms: float | None = None
    total_ms: float | None = None
    interrupted: bool = False
    llm_prompt_tokens: int = 0
    llm_cached_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_output_characters: int = 0
    tts_characters: int = 0

    @property
    def tts_net_ttfa_ms(self) -> float | None:
        """TTS time to first audio measured from the first LLM token.

        Raw ``tts_first_audio_ms`` is measured from the start of the turn and
        therefore includes LLM TTFT. Subtracting it isolates the TTS leg while
        the raw value stays available for diagnosis.
        """
        if self.tts_first_audio_ms is None or self.llm_first_token_ms is None:
            return None
        return max(0.0, self.tts_first_audio_ms - self.llm_first_token_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "turn_id": self.turn_id,
            "kind": self.kind,
            "user_text": self.user_text,
            "assistant_text": self.assistant_text,
            "asr_final_delay_ms": _r(self.asr_final_delay_ms),
            "llm_first_token_ms": _r(self.llm_first_token_ms),
            "tts_first_audio_ms": _r(self.tts_first_audio_ms),
            "tts_net_ttfa_ms": _r(self.tts_net_ttfa_ms),
            "first_audio_out_ms": _r(self.first_audio_out_ms),
            "total_ms": _r(self.total_ms),
            "interrupted": self.interrupted,
            "llm_prompt_tokens": self.llm_prompt_tokens,
            "llm_completion_tokens": self.llm_completion_tokens,
            "llm_output_characters": self.llm_output_characters,
            "tts_characters": self.tts_characters,
        }


def _r(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None


@dataclass
class CallRecord:
    call_id: str
    direction: str  # inbound | outbound | browser
    from_number: str = ""
    to_number: str = ""
    voice_id: str = ""
    tts_provider: str = ""
    tts_model: str = ""
    asr_model: str = ""
    llm_model: str = ""
    status: str = "active"
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    end_reason: str = ""
    turns: list[TurnMetrics] = field(default_factory=list)
    asr_seconds: float = 0.0
    asr_session_seconds: float = 0.0
    llm_prompt_tokens: int = 0
    llm_cached_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_output_characters: int = 0
    tts_characters: int = 0
    tts_audio_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        end = self.ended_at or time.time()
        return max(0.0, end - self.started_at)

    @property
    def avg_response_ms(self) -> float | None:
        values = [
            t.total_ms for t in self.turns if t.total_ms is not None and t.kind == "response"
        ]
        return round(sum(values) / len(values), 1) if values else None

    def finish(self, reason: str) -> None:
        if self.ended_at is None:
            self.ended_at = time.time()
        self.status = "completed"
        self.end_reason = reason

    def add_error(self, message: str) -> None:
        self.errors.append(message)
        self.errors = self.errors[-20:]

    @property
    def billed_asr_seconds(self) -> float:
        """AssemblyAI bills the streaming session, not only spoken audio."""
        return self.asr_session_seconds or self.asr_seconds or self.duration_seconds

    def costs(self, settings) -> dict[str, float | None]:
        rates = settings.pricing()
        asr_seconds = self.billed_asr_seconds
        asr = _cost(asr_seconds, rates["asr_per_minute"], 60.0)
        llm = llm_cost(
            self.llm_prompt_tokens,
            self.llm_cached_tokens,
            self.llm_completion_tokens,
            self.llm_output_characters,
            rates,
        )
        tts = _cost(
            self.tts_characters, settings.tts_rate(self.tts_provider, self.tts_model), 1_000_000.0
        )
        total = (
            round(asr + llm + tts, 6)
            if asr is not None and llm is not None and tts is not None
            else None
        )
        usd_inr = rates.get("usd_inr", 95.0)
        return {
            "asr": asr,
            "asr_billed_seconds": round(asr_seconds, 2),
            "llm": llm,
            "tts": tts,
            "total": total,
            "usd_inr": usd_inr,
            "total_inr": round(total * usd_inr, 2) if total is not None else None,
        }

    def to_dict(self, settings) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "direction": self.direction,
            "from_number": self.from_number,
            "to_number": self.to_number,
            "voice_id": self.voice_id,
            "tts_provider": self.tts_provider,
            "tts_model": self.tts_model,
            "asr_model": self.asr_model,
            "llm_model": self.llm_model,
            "status": self.status,
            "started_at": datetime.fromtimestamp(self.started_at, timezone.utc).isoformat(
                timespec="seconds"
            ),
            "duration_seconds": round(self.duration_seconds, 1),
            "asr_seconds": round(self.asr_seconds, 2),
            "asr_session_seconds": round(self.asr_session_seconds, 2),
            "asr_billed_seconds": round(self.billed_asr_seconds, 2),
            "llm_prompt_tokens": self.llm_prompt_tokens,
            "llm_cached_tokens": self.llm_cached_tokens,
            "llm_completion_tokens": self.llm_completion_tokens,
            "llm_output_characters": self.llm_output_characters,
            "tts_characters": self.tts_characters,
            "tts_audio_seconds": round(self.tts_audio_seconds, 2),
            "avg_response_ms": self.avg_response_ms,
            "end_reason": self.end_reason,
            "errors": self.errors,
            "costs": self.costs(settings),
            "turns": [turn.to_dict() for turn in self.turns],
        }


class SessionRegistry:
    def __init__(self) -> None:
        self.calls: dict[str, CallRecord] = {}
        self.active: dict[str, Any] = {}  # call_id -> VoiceAgent

    def create(
        self,
        direction: str,
        *,
        from_number: str = "",
        to_number: str = "",
        voice_id: str = "",
        tts_provider: str = "",
        tts_model: str = "",
        call_id: str | None = None,
    ) -> CallRecord:
        record = CallRecord(
            call_id=call_id or f"call_{uuid.uuid4().hex[:12]}",
            direction=direction,
            from_number=from_number,
            to_number=to_number,
            voice_id=voice_id,
            tts_provider=tts_provider,
            tts_model=tts_model,
        )
        self.calls[record.call_id] = record
        return record

    def attach_agent(self, call_id: str, agent: Any) -> None:
        self.active[call_id] = agent

    def detach_agent(self, call_id: str) -> None:
        self.active.pop(call_id, None)

    def get(self, call_id: str) -> CallRecord | None:
        return self.calls.get(call_id)

    def stats(self, settings) -> dict[str, Any]:
        records = list(self.calls.values())
        completed = [r for r in records if r.ended_at is not None]
        totals = {
            "calls": len(records),
            "active_calls": len(self.active),
            "completed_calls": len(completed),
            "duration_seconds": round(sum(r.duration_seconds for r in records), 1),
            "asr_seconds": round(sum(r.asr_seconds for r in records), 2),
            "asr_billed_seconds": round(sum(r.billed_asr_seconds for r in records), 2),
            "llm_prompt_tokens": sum(r.llm_prompt_tokens for r in records),
            "llm_cached_tokens": sum(r.llm_cached_tokens for r in records),
            "llm_completion_tokens": sum(r.llm_completion_tokens for r in records),
            "llm_output_characters": sum(r.llm_output_characters for r in records),
            "tts_characters": sum(r.tts_characters for r in records),
            "tts_audio_seconds": round(sum(r.tts_audio_seconds for r in records), 2),
            "turns": sum(len(r.turns) for r in records),
        }
        totals["asr_minutes"] = round(totals["asr_billed_seconds"] / 60.0, 3)
        totals["total_characters"] = (
            totals["llm_output_characters"] + totals["tts_characters"]
        )

        rates = settings.pricing()
        asr_cost = _cost(totals["asr_billed_seconds"], rates["asr_per_minute"], 60.0)
        llm_parts = [
            llm_cost(
                r.llm_prompt_tokens,
                r.llm_cached_tokens,
                r.llm_completion_tokens,
                r.llm_output_characters,
                rates,
            )
            for r in records
        ]
        llm_total = (
            None if any(part is None for part in llm_parts) else round(sum(llm_parts), 6)
        )
        tts_parts = [
            _cost(
                r.tts_characters, settings.tts_rate(r.tts_provider, r.tts_model), 1_000_000.0
            )
            for r in records
        ]
        tts_cost = (
            None if any(part is None for part in tts_parts) else round(sum(tts_parts), 6)
        )
        total_cost = (
            round(asr_cost + llm_total + tts_cost, 6)
            if asr_cost is not None and llm_total is not None and tts_cost is not None
            else None
        )
        usd_inr = rates.get("usd_inr", 95.0)

        all_turns = [
            turn
            for record in records
            for turn in record.turns
            if turn.kind == "response"
        ]

        def average(attr: str) -> float | None:
            values = [getattr(t, attr) for t in all_turns if getattr(t, attr) is not None]
            return round(sum(values) / len(values), 1) if values else None

        latency = {
            "asr_final_ms": average("asr_final_delay_ms"),
            "llm_first_token_ms": average("llm_first_token_ms"),
            "tts_first_audio_ms": average("tts_first_audio_ms"),
            "tts_net_ttfa_ms": average("tts_net_ttfa_ms"),
            "first_audio_out_ms": average("first_audio_out_ms"),
            "total_ms": average("total_ms"),
            "turns": len(all_turns),
        }

        # Newest first, capped for the dashboard.
        recent = sorted(records, key=lambda r: r.started_at, reverse=True)[:50]
        return {
            "generated_at": utc_now_iso(),
            "totals": totals,
            "costs": {
                "asr": asr_cost,
                "llm": llm_total,
                "tts": tts_cost,
                "total": total_cost,
                "usd_inr": usd_inr,
                "total_inr": (
                    round(total_cost * usd_inr, 2)
                    if total_cost is not None
                    else None
                ),
                "rates_configured": settings.pricing_configured(),
                "rates": rates,
            },
            "latency": latency,
            "calls": [r.to_dict(settings) for r in recent],
        }


class EventBus:
    """Fan-out queue used by the dashboard WebSocket.

    Slow consumers drop events instead of blocking the audio pipeline.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        event.setdefault("ts", utc_now_iso())
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("event_bus_drop", extra={"event_type": event.get("type")})

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


registry = SessionRegistry()
event_bus = EventBus()
