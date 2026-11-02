"""Logging used by every backend module.

Two console formats (``LOG_FORMAT``): ``pretty`` (default) prints one readable
line per event, ``json`` prints one JSON object per line for log tooling.  Log
records are handed to a background thread through a queue, so a slow terminal
can never block the event loop that carries call audio.
"""

from __future__ import annotations

import atexit
import copy
import json
import logging
import logging.handlers
import os
import queue
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable

_STANDARD_ATTRS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
    "message",
}

# Polled by the dashboard every few seconds; logging each hit buries real events.
_QUIET_PATHS = ("/api/stats", "/api/health", "/api/warmup", "/favicon.ico")
_REPEAT_404_SECONDS = 300


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _STANDARD_ATTRS and not key.startswith("_")
    }


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in _extras(record).items():
            try:
                json.dumps(value)
                payload[key] = value
            except TypeError:
                payload[key] = str(value)
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


# --------------------------------------------------------------------- pretty
def _ms(value: Any) -> str:
    return f"{float(value):.0f}ms"


def _secs(value: Any) -> str:
    seconds = int(round(float(value)))
    return f"{seconds // 60}m{seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def _turn_complete(e: dict[str, Any]) -> str:
    parts = []
    if e.get("asr_final_delay_ms"):
        parts.append(f"asr {_ms(e['asr_final_delay_ms'])}")
    if e.get("llm_first_token_ms"):
        parts.append(f"llm {_ms(e['llm_first_token_ms'])}")
    if e.get("tts_net_ttfa_ms") is not None:
        parts.append(f"tts {_ms(e['tts_net_ttfa_ms'])}")
    if e.get("total_ms"):
        parts.append(f"first audio at {_ms(e['total_ms'])}")
    if e.get("tts_characters"):
        parts.append(f"{e['tts_characters']} chars")
    head = "turn interrupted" if e.get("interrupted") else "turn done"
    if not parts and e.get("interrupted"):
        parts.append("before any audio")
    line = f"{head} · " + " · ".join(parts) if parts else head
    if e.get("user"):
        line += f'\n    caller: "{e["user"]}"'
    if e.get("agent"):
        line += f'\n    agent:  "{e["agent"]}"'
    return line


def _call_ended(e: dict[str, Any]) -> str:
    parts = [f"call ended ({e.get('reason')})"]
    if e.get("duration_seconds") is not None:
        parts.append(_secs(e["duration_seconds"]))
    parts.append(f"{e.get('turns', 0)} turns")
    if e.get("avg_response_ms"):
        parts.append(f"avg response {_ms(e['avg_response_ms'])}")
    if e.get("tts_characters"):
        parts.append(f"{e['tts_characters']} TTS chars")
    if e.get("cost_usd") is not None:
        parts.append(f"≈${e['cost_usd']:.4f} (est.)")
    return " · ".join(parts)


def _server_ready(e: dict[str, Any]) -> str:
    took = f" · warm-up took {_secs(e['duration_ms'] / 1000)}" if e.get("duration_ms") else ""
    blockers = "; ".join(e.get("blockers") or [])
    where = f" · dashboard {e['dashboard']}" if e.get("dashboard") else ""
    if e.get("state") == "ready":
        return (
            f"✔ READY · everything is set up · voice {e.get('tts')}{took}\n"
            f"    Open the dashboard and try a call (web, inbound or outbound){where}"
        )
    if e.get("state") == "web_only":
        return (
            f"✔ READY for web calls · phone calls not ready: {blockers}{took}\n"
            f"    Try a web call from the dashboard{where}"
        )
    return (
        f"✖ NOT READY · calls will fail until this is fixed: {blockers}{took}\n"
        f"    Check Warm-up status in the dashboard, then re-run it{where}"
    )


_TEMPLATES: dict[str, Callable[[dict[str, Any]], str]] = {
    "server_ready": _server_ready,
    "turn_complete": _turn_complete,
    "call_ended": _call_ended,
    "call_started": lambda e: (
        f"call started · {e.get('direction')} · {e.get('from') or '?'} → {e.get('to') or '?'}"
    ),
    "barge_in": lambda e: (
        f"barge-in while {e.get('state')} · "
        f"{_ms(e.get('playout_remaining_ms') or 0)} of queued audio cleared"
    ),
    "sarvam_first_token": lambda e: (
        f"Sarvam LLM first token {_ms(e['first_token_ms'])} "
        f"(response started after {_ms(e['headers_ms'])}) {e.get('token')!r}"
    ),
    "sarvam_tts_first_audio": lambda e: (
        f"Sarvam TTS first audio {_ms(e['request_to_first_audio_ms'])}"
    ),
    "speechify_first_audio": lambda e: (
        f"Speechify first audio {_ms(e['request_to_first_audio_ms'])} "
        f"(server ttfb {e.get('server_ttfb_ms')}ms, model {e.get('model_ms')}ms)"
    ),
}
# Keys shown in the line prefix or already folded into a template.
_PREFIX_KEYS = {"call_id", "turn"}

_COLORS = {
    "DEBUG": "\033[90m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[1;31m",
}
_DIM, _RESET = "\033[2m", "\033[0m"


class PrettyFormatter(logging.Formatter):
    def __init__(self, color: bool) -> None:
        super().__init__()
        self.color = color

    def _paint(self, text: str, code: str) -> str:
        return f"{code}{text}{_RESET}" if self.color else text

    def format(self, record: logging.LogRecord) -> str:
        extras = _extras(record)
        message = record.getMessage()
        template = _TEMPLATES.get(message)
        if template is not None:
            try:
                body = template(extras)
            except (KeyError, TypeError, ValueError):
                template = None
        if template is None:
            body = message.replace("_", " ") if record.name != "uvicorn.access" else message
            rest = {k: v for k, v in extras.items() if k not in _PREFIX_KEYS}
            if rest:
                body += " · " + " ".join(f"{k}={v}" for k, v in rest.items())

        stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        stamp += f".{int(record.msecs):03d}"
        level = self._paint(f"{record.levelname:<5}", _COLORS.get(record.levelname, ""))
        tag = ""
        call_id = extras.get("call_id")
        if call_id:
            tag = str(call_id).removeprefix("call_")[:6]
            if extras.get("turn") is not None:
                tag += f" T{extras['turn']}"
            tag = self._paint(f"[{tag}] ", _DIM)
        line = f"{self._paint(stamp, _DIM)} {level} {tag}{body}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


# --------------------------------------------------------------- access filter
class AccessLogFilter(logging.Filter):
    """Drop dashboard polling, and report unknown paths once instead of forever."""

    def __init__(self) -> None:
        super().__init__()
        self._seen_404: dict[str, float] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return True
        client, _method, path, _version, status = args[:5]
        path = str(path).split("?", 1)[0]
        status = int(status)
        if status < 400 and path.startswith(_QUIET_PATHS):
            return False
        if status == 404:
            now = time.monotonic()
            last = self._seen_404.get(path)
            if last is not None and now - last < _REPEAT_404_SECONDS:
                return False
            self._seen_404[path] = now
            record.msg = f"unknown path {path} requested by {client} (404); repeats hidden for 5 min"
            record.args = None
            record.levelno, record.levelname = logging.WARNING, "WARNING"
        return True


class _NonBlockingQueueHandler(logging.handlers.QueueHandler):
    """Queue records as-is; the listener thread does the formatting and I/O."""

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        record = copy.copy(record)
        record.message = record.getMessage()
        record.msg, record.args = record.message, None
        return record


_listener: logging.handlers.QueueListener | None = None


def _stop_listener() -> None:
    global _listener
    listener, _listener = _listener, None
    if listener is not None:
        listener.stop()  # flushes queued records before exiting


atexit.register(_stop_listener)


def setup_logging(level: str = "INFO", fmt: str = "pretty") -> None:
    global _listener
    _stop_listener()

    stream = sys.stdout
    use_color = stream.isatty() and not os.getenv("NO_COLOR")
    console = logging.StreamHandler(stream)
    console.setFormatter(
        JsonFormatter() if fmt.lower() == "json" else PrettyFormatter(use_color)
    )

    log_queue: queue.SimpleQueue = queue.SimpleQueue()
    _listener = logging.handlers.QueueListener(log_queue, console)
    _listener.start()

    root = logging.getLogger()
    root.handlers = [_NonBlockingQueueHandler(log_queue)]
    root.setLevel(level.upper())

    # Uvicorn keeps its own handlers unless silenced; keep one pipeline.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.filters = [f for f in access.filters if not isinstance(f, AccessLogFilter)]
    if root.level > logging.DEBUG:
        access.addFilter(AccessLogFilter())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
