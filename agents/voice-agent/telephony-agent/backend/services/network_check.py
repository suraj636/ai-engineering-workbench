"""Network quality check from this server to every provider endpoint.

Measures what matters for a streaming voice agent: how fast new connections
open (DNS, TCP, TLS), how consistent that is (jitter), whether anything
stalls or fails.  It only opens TCP/TLS connections: no API keys, no provider
requests, no cost.  It measures the path from *this machine* to the providers,
not Plivo's media path into this server.
"""

from __future__ import annotations

import asyncio
import logging
import random
import socket
import ssl
import statistics
import string
import time
from datetime import datetime, timezone
from typing import Any


log = logging.getLogger("network_check")

SAMPLES = 5  # connections per host
DNS_SAMPLES = 3  # lookups per host
TIMEOUT_S = 8.0
OVERALL_TIMEOUT_S = 25.0

HOSTS = [
    ("Plivo", "api.plivo.com"),
    ("AssemblyAI", "streaming.assemblyai.com"),
    ("Sarvam", "api.sarvam.ai"),
    ("Inworld", "api.inworld.ai"),
    ("Speechify", "api.speechify.ai"),
]

# (good_below, fair_below) per metric; anything above "fair" is bad.
THRESHOLDS = {
    "dns": (100, 300),
    "connect": (80, 200),
    "tls": (250, 600),
    "jitter": (20, 50),
    "failures": (0.0001, 10),  # percent; good only when nothing failed
    "stall": (500, 1500),
}
WEIGHTS = {"dns": 0.2, "connect": 0.2, "tls": 0.1, "jitter": 0.15, "failures": 0.2, "stall": 0.15}
RATING_POINTS = {"good": 100, "fair": 60, "bad": 20}

# key: (label, unit, what it is, why it matters at each hop of the agent)
PARAMS = {
    "dns": (
        "DNS lookup",
        "ms",
        "Time to turn a provider's name (like api.sarvam.ai) into an address.",
        "Call connect: when a call starts, the server opens new connections to AssemblyAI, the LLM and "
        "the TTS, and each begins with this lookup. A slow lookup delays the greeting and the first reply.",
    ),
    "connect": (
        "Connection round trip",
        "ms",
        "Time for a packet to reach a provider and the answer to come back.",
        "Caller speaks: every audio chunk goes from this server to AssemblyAI. Agent speaks: every LLM "
        "token and TTS audio chunk comes back. It is added on each hop, so it sets how fast the agent can answer.",
    ),
    "tls": (
        "Secure handshake",
        "ms",
        "Time to set up the encrypted channel once connected.",
        "Call connect: paid once per provider connection when a call starts, and again whenever one "
        "reconnects (for example Sarvam TTS after the caller interrupts).",
    ),
    "jitter": (
        "Jitter",
        "ms",
        "How much the round-trip time varies from one attempt to the next.",
        "Caller speaks: audio reaching the ASR unevenly delays end-of-turn detection. Agent speaks: "
        "TTS chunks arriving unevenly make the voice choppy on the phone.",
    ),
    "failures": (
        "Connection failures",
        "%",
        "Share of connection attempts that timed out or were refused (a stand-in for packet loss).",
        "A failure means a lost transcript, a missing reply or a dead line. That is worse than slowness.",
    ),
    "stall": (
        "Worst stall",
        "ms",
        "The single slowest step seen in this check.",
        "One stall during a call is what the caller hears as a long pause. Averages hide it.",
    ),
}


def _rating(metric: str, value: float) -> str:
    good, fair = THRESHOLDS[metric]
    if value < good:
        return "good"
    return "fair" if value < fair else "bad"


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


async def _dns_lookup(host: str) -> tuple[float, bool]:
    """Time a lookup of the real provider name, as the app itself would do it.

    Answers may come from the local resolver cache, so a stall only shows up
    when the cache is cold (new calls after the entry expired); that is exactly
    the case that delays a call start.
    """
    loop = asyncio.get_running_loop()
    started = time.perf_counter()
    try:
        await asyncio.wait_for(loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM), TIMEOUT_S)
    except (asyncio.TimeoutError, OSError):
        return (time.perf_counter() - started) * 1000, False
    return (time.perf_counter() - started) * 1000, True


async def _connect(host: str, ip_family_addr: tuple[int, str]) -> tuple[float | None, float | None]:
    """Return (tcp_ms, tls_ms); None for a step that failed."""
    family, ip = ip_family_addr
    writer = None
    try:
        started = time.perf_counter()
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, 443, family=family), TIMEOUT_S
        )
        tcp_ms = (time.perf_counter() - started) * 1000
        mid = time.perf_counter()
        await asyncio.wait_for(
            writer.start_tls(ssl.create_default_context(), server_hostname=host), TIMEOUT_S
        )
        return tcp_ms, (time.perf_counter() - mid) * 1000
    except (asyncio.TimeoutError, OSError, ssl.SSLError):
        elapsed = (time.perf_counter() - started) * 1000
        return (None, None) if writer is None else (elapsed, None)
    finally:
        if writer is not None:
            writer.close()


async def _check_host(label: str, host: str) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    dns_ms: list[float] = []
    attempts = failures = 0
    for _ in range(DNS_SAMPLES):
        ms, ok = await _dns_lookup(host)
        dns_ms.append(ms)
        attempts += 1
        failures += 0 if ok else 1

    tcp_ms: list[float] = []
    tls_ms: list[float] = []
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM), TIMEOUT_S
        )
        target = (infos[0][0], infos[0][4][0])
    except (asyncio.TimeoutError, OSError):
        target = None
        failures += SAMPLES
        attempts += SAMPLES

    if target:
        for _ in range(SAMPLES):
            tcp, tls = await _connect(host, target)
            attempts += 1
            if tcp is not None:
                tcp_ms.append(tcp)
            if tls is not None:
                tls_ms.append(tls)
            if tcp is None or tls is None:
                failures += 1
            await asyncio.sleep(0.12)

    diffs = [abs(b - a) for a, b in zip(tcp_ms, tcp_ms[1:])]
    return {
        "label": label,
        "host": host,
        "dns_ms": _median(dns_ms),
        "connect_ms": _median(tcp_ms),
        "tls_ms": _median(tls_ms),
        "jitter_ms": statistics.mean(diffs) if diffs else None,
        "failures": failures,
        "attempts": attempts,
        "worst_ms": max(dns_ms + tcp_ms + tls_ms, default=None),
    }


def _aggregate(hosts: list[dict[str, Any]]) -> dict[str, Any]:
    def pool(key: str) -> list[float]:
        return [h[key] for h in hosts if h[key] is not None]

    attempts = sum(h["attempts"] for h in hosts)
    failed = sum(h["failures"] for h in hosts)
    values: dict[str, float] = {
        "dns": _median(pool("dns_ms")),
        "connect": _median(pool("connect_ms")),
        "tls": _median(pool("tls_ms")),
        "jitter": _median(pool("jitter_ms")),
        "failures": (failed / attempts * 100) if attempts else 100.0,
        "stall": max(pool("worst_ms"), default=None),
    }
    params = []
    score = 0.0
    for key, (label, unit, what, why) in PARAMS.items():
        value = values[key]
        # An unmeasurable metric (everything failed) is bad, not skipped.
        rating = _rating(key, value) if value is not None else "bad"
        score += RATING_POINTS[rating] * WEIGHTS[key]
        params.append(
            {
                "key": key,
                "label": label,
                "unit": unit,
                "value": round(value, 1) if value is not None else None,
                "rating": rating,
                "what": what,
                "why": why,
            }
        )
    score = round(score)
    ratings = {p["key"]: p["rating"] for p in params}
    # Averages hide the problems callers actually hear, so they cap the score:
    # one multi-second stall is a long silence, and failed connections are a dead line.
    if ratings["failures"] == "bad":
        score = min(score, 49)
    elif ratings["stall"] == "bad":
        score = min(score, 69)
    label = "Good" if score >= 80 else "Fair" if score >= 50 else "Poor"
    advice = {
        "Good": "This connection is suitable for real-time voice calls.",
        "Fair": "Usable, but expect occasional slow turns. Re-run during a stall to see which step fails.",
        "Poor": "Likely to cause long pauses or dropped calls. Try another network, a wired link, or a different DNS server.",
    }[label]
    if ratings["stall"] == "bad" and label != "Poor":
        advice = "A multi-second stall was seen, which a caller hears as a long pause. Typical speed is fine, so this is intermittent: check DNS and Wi-Fi."
    return {"score": score, "label": label, "advice": advice, "params": params}


class NetworkChecker:
    """Runs one check at a time and remembers the latest result."""

    def __init__(self) -> None:
        self._last: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def snapshot(self) -> dict[str, Any]:
        return {"running": self.running, "result": self._last}

    async def run(self) -> dict[str, Any]:
        if not self.running:
            self._task = asyncio.create_task(self._measure(), name="network-check")
        try:
            return await asyncio.shield(self._task)
        except asyncio.CancelledError:
            raise

    async def _measure(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            hosts = await asyncio.wait_for(
                asyncio.gather(*(_check_host(label, host) for label, host in HOSTS)),
                OVERALL_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            hosts = []
        result = _aggregate(list(hosts)) if hosts else _aggregate(
            [
                {"label": l, "host": h, "dns_ms": None, "connect_ms": None, "tls_ms": None,
                 "jitter_ms": None, "failures": 1, "attempts": 1, "worst_ms": OVERALL_TIMEOUT_S * 1000}
                for l, h in HOSTS
            ]
        )
        result["hosts"] = [
            {k: (round(v, 1) if isinstance(v, float) else v) for k, v in h.items()} for h in hosts
        ]
        result["measured_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        result["duration_ms"] = round((time.perf_counter() - started) * 1000)
        self._last = result
        log.info(
            "network_check",
            extra={"score": result["score"], "label": result["label"], "duration_ms": result["duration_ms"]},
        )
        return result


network_checker = NetworkChecker()
