"""SQLite call history for the Cost & latency page.

Standard-library ``sqlite3`` only. A call is written once, when it ends (the
caller runs ``save_call`` in a worker thread so a live call is never delayed).
Cost is stored with the rates in force at that moment, so changing a price
later does not rewrite history. Each operation opens its own short-lived
connection, which keeps the module safe to call from any thread.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import statistics
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("call_db")

SCHEMA_VERSION = 2
PER_TYPE_LIMIT = 10
# API tab name -> stored direction.
TYPES = {"inbound": "inbound", "outbound": "outbound", "web": "browser"}
# Fewest samples before a per-call median (and a p95) is considered meaningful.
MIN_MEDIAN_TURNS = 2
MIN_P95_TURNS = 5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    call_id TEXT PRIMARY KEY,
    direction TEXT NOT NULL,
    started_ts REAL NOT NULL,
    started_at TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    end_reason TEXT NOT NULL DEFAULT '',
    from_number TEXT NOT NULL DEFAULT '',
    to_number TEXT NOT NULL DEFAULT '',
    asr_model TEXT NOT NULL DEFAULT '',
    llm_model TEXT NOT NULL DEFAULT '',
    tts_provider TEXT NOT NULL DEFAULT '',
    tts_model TEXT NOT NULL DEFAULT '',
    voice_id TEXT NOT NULL DEFAULT '',
    asr_seconds REAL NOT NULL DEFAULT 0,
    asr_session_seconds REAL NOT NULL DEFAULT 0,
    asr_billed_seconds REAL NOT NULL DEFAULT 0,
    llm_prompt_tokens INTEGER NOT NULL DEFAULT 0,
    llm_cached_tokens INTEGER NOT NULL DEFAULT 0,
    llm_completion_tokens INTEGER NOT NULL DEFAULT 0,
    llm_output_characters INTEGER NOT NULL DEFAULT 0,
    tts_characters INTEGER NOT NULL DEFAULT 0,
    tts_audio_seconds REAL NOT NULL DEFAULT 0,
    cost_asr REAL,
    cost_llm REAL,
    cost_tts REAL,
    cost_total REAL,
    usd_inr REAL,
    avg_response_ms REAL,
    errors_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_calls_dir_started ON calls (direction, started_ts DESC);

CREATE TABLE IF NOT EXISTS turns (
    call_id TEXT NOT NULL REFERENCES calls (call_id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    turn_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'response',
    user_text TEXT NOT NULL DEFAULT '',
    assistant_text TEXT NOT NULL DEFAULT '',
    interrupted INTEGER NOT NULL DEFAULT 0,
    asr_final_delay_ms REAL,
    llm_first_token_ms REAL,
    tts_first_audio_ms REAL,
    tts_net_ttfa_ms REAL,
    first_audio_out_ms REAL,
    total_ms REAL,
    llm_prompt_tokens INTEGER NOT NULL DEFAULT 0,
    llm_completion_tokens INTEGER NOT NULL DEFAULT 0,
    llm_output_characters INTEGER NOT NULL DEFAULT 0,
    tts_characters INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (call_id, idx)
);
"""

_TURN_COLUMNS = (
    "idx", "turn_id", "kind", "user_text", "assistant_text", "interrupted",
    "asr_final_delay_ms", "llm_first_token_ms", "tts_first_audio_ms",
    "tts_net_ttfa_ms", "first_audio_out_ms", "total_ms", "llm_prompt_tokens",
    "llm_completion_tokens", "llm_output_characters", "tts_characters",
)

_db_path: Path | None = None


def init(path: str | Path) -> None:
    """Create the database file and schema. Never raises: history is optional."""
    global _db_path
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(_connect(target)) as conn:
            conn.executescript(_SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(calls)")}
            if "llm_cached_tokens" not in columns:  # v1 database
                conn.execute(
                    "ALTER TABLE calls ADD COLUMN llm_cached_tokens INTEGER NOT NULL DEFAULT 0"
                )
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
        _db_path = target
        log.info("call_db_ready", extra={"path": str(target)})
    except Exception as exc:  # noqa: BLE001 - the app runs fine without history
        _db_path = None
        log.warning("call_db_unavailable", extra={"error": str(exc)})


def enabled() -> bool:
    return _db_path is not None


def _connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or _db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# ------------------------------------------------------------------- writing
def save_call(summary: dict[str, Any], *, asr_model: str, llm_model: str, started_ts: float) -> None:
    """Persist one finished call (the dict produced by ``CallRecord.to_dict``)."""
    if _db_path is None:
        return
    costs = summary.get("costs") or {}
    try:
        with closing(_connect()) as conn, conn:
            conn.execute("DELETE FROM calls WHERE call_id = ?", (summary["call_id"],))
            conn.execute(
                """INSERT INTO calls (
                    call_id, direction, started_ts, started_at, duration_seconds, end_reason,
                    from_number, to_number, asr_model, llm_model, tts_provider, tts_model,
                    voice_id, asr_seconds, asr_session_seconds, asr_billed_seconds,
                    llm_prompt_tokens, llm_cached_tokens, llm_completion_tokens,
                    llm_output_characters, tts_characters, tts_audio_seconds, cost_asr, cost_llm, cost_tts,
                    cost_total, usd_inr, avg_response_ms, errors_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    summary["call_id"], summary.get("direction", ""), started_ts,
                    summary.get("started_at", ""), summary.get("duration_seconds") or 0,
                    summary.get("end_reason", ""), summary.get("from_number", ""),
                    summary.get("to_number", ""), asr_model, llm_model,
                    summary.get("tts_provider", ""), summary.get("tts_model", ""),
                    summary.get("voice_id", ""), summary.get("asr_seconds") or 0,
                    summary.get("asr_session_seconds") or 0,
                    summary.get("asr_billed_seconds") or 0,
                    summary.get("llm_prompt_tokens") or 0,
                    summary.get("llm_cached_tokens") or 0,
                    summary.get("llm_completion_tokens") or 0,
                    summary.get("llm_output_characters") or 0,
                    summary.get("tts_characters") or 0,
                    summary.get("tts_audio_seconds") or 0,
                    costs.get("asr"), costs.get("llm"), costs.get("tts"), costs.get("total"),
                    costs.get("usd_inr"), summary.get("avg_response_ms"),
                    json.dumps(summary.get("errors") or []),
                ),
            )
            placeholders = ",".join("?" * (len(_TURN_COLUMNS) + 1))
            conn.executemany(
                f"INSERT INTO turns (call_id, {', '.join(_TURN_COLUMNS)}) VALUES ({placeholders})",
                [
                    (
                        summary["call_id"],
                        turn.get("index", position),
                        turn.get("turn_id", ""),
                        turn.get("kind", "response"),
                        turn.get("user_text", ""),
                        turn.get("assistant_text", ""),
                        1 if turn.get("interrupted") else 0,
                        turn.get("asr_final_delay_ms"),
                        turn.get("llm_first_token_ms"),
                        turn.get("tts_first_audio_ms"),
                        turn.get("tts_net_ttfa_ms"),
                        turn.get("first_audio_out_ms"),
                        turn.get("total_ms"),
                        turn.get("llm_prompt_tokens") or 0,
                        turn.get("llm_completion_tokens") or 0,
                        turn.get("llm_output_characters") or 0,
                        turn.get("tts_characters") or 0,
                    )
                    for position, turn in enumerate(summary.get("turns") or [])
                ],
            )
        log.info("call_saved", extra={"call_id": summary["call_id"]})
    except Exception as exc:  # noqa: BLE001 - must never affect a call
        log.warning("call_save_failed", extra={"call_id": summary.get("call_id"), "error": str(exc)})


# ------------------------------------------------------------------- reading
def _call_dict(row: sqlite3.Row, turns: list[sqlite3.Row]) -> dict[str, Any]:
    """Rebuild the same shape as ``CallRecord.to_dict`` so the UI reuses it."""
    usd_inr = row["usd_inr"] or 95.0
    total = row["cost_total"]
    return {
        "call_id": row["call_id"],
        "direction": row["direction"],
        "from_number": row["from_number"],
        "to_number": row["to_number"],
        "voice_id": row["voice_id"],
        "tts_provider": row["tts_provider"],
        "tts_model": row["tts_model"],
        "asr_model": row["asr_model"],
        "llm_model": row["llm_model"],
        "status": "completed",
        "started_at": row["started_at"],
        "duration_seconds": row["duration_seconds"],
        "asr_seconds": row["asr_seconds"],
        "asr_session_seconds": row["asr_session_seconds"],
        "asr_billed_seconds": row["asr_billed_seconds"],
        "llm_prompt_tokens": row["llm_prompt_tokens"],
        "llm_cached_tokens": row["llm_cached_tokens"],
        "llm_completion_tokens": row["llm_completion_tokens"],
        "llm_output_characters": row["llm_output_characters"],
        "tts_characters": row["tts_characters"],
        "tts_audio_seconds": row["tts_audio_seconds"],
        "avg_response_ms": row["avg_response_ms"],
        "end_reason": row["end_reason"],
        "errors": json.loads(row["errors_json"] or "[]"),
        "costs": {
            "asr": row["cost_asr"],
            "asr_billed_seconds": row["asr_billed_seconds"],
            "llm": row["cost_llm"],
            "tts": row["cost_tts"],
            "total": total,
            "usd_inr": usd_inr,
            "total_inr": round(total * usd_inr, 2) if total is not None else None,
        },
        "turns": [
            {**{key: turn[key] for key in _TURN_COLUMNS}, "index": turn["idx"],
             "interrupted": bool(turn["interrupted"])}
            for turn in turns
        ],
        "saved": True,
    }


def get_call(call_id: str) -> dict[str, Any] | None:
    if _db_path is None:
        return None
    try:
        with closing(_connect()) as conn:
            row = conn.execute("SELECT * FROM calls WHERE call_id = ?", (call_id,)).fetchone()
            if row is None:
                return None
            turns = conn.execute(
                "SELECT * FROM turns WHERE call_id = ? ORDER BY idx", (call_id,)
            ).fetchall()
            return _call_dict(row, turns)
    except Exception as exc:  # noqa: BLE001
        log.warning("call_read_failed", extra={"call_id": call_id, "error": str(exc)})
        return None


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _latency(values: list[float]) -> dict[str, Any]:
    """Median (and p95 once there are enough samples) with the sample size."""
    count = len(values)
    return {
        "count": count,
        "median": round(statistics.median(values), 1) if count >= 1 else None,
        "p95": round(_percentile(values, 0.95), 1) if count >= MIN_P95_TURNS else None,
    }


def _turn_totals(turns: list[sqlite3.Row]) -> list[float]:
    """Per-turn total = TTFT + TTFA, for turns where both were measured."""
    return [
        t["llm_first_token_ms"] + t["tts_net_ttfa_ms"]
        for t in turns
        if t["llm_first_token_ms"] is not None and t["tts_net_ttfa_ms"] is not None
    ]


def _valid_turns(turns: list[sqlite3.Row]) -> list[sqlite3.Row]:
    # Same rule as the dashboard: completed response turns only. The greeting
    # and interrupted turns are not representative of normal latency.
    return [t for t in turns if t["kind"] == "response" and not t["interrupted"]]


def history(kind: str = "all") -> dict[str, Any]:
    """Last 10 calls per type (or of one type) plus the summary over them."""
    empty = {"type": kind, "calls": [], "summary": _summarize([], {}), "enabled": enabled()}
    if _db_path is None:
        return empty
    wanted = list(TYPES.values()) if kind == "all" else [TYPES.get(kind, "")]
    try:
        with closing(_connect()) as conn:
            rows: list[sqlite3.Row] = []
            for direction in wanted:
                rows += conn.execute(
                    "SELECT * FROM calls WHERE direction = ? ORDER BY started_ts DESC LIMIT ?",
                    (direction, PER_TYPE_LIMIT),
                ).fetchall()
            rows.sort(key=lambda r: r["started_ts"], reverse=True)
            turns_by_call: dict[str, list[sqlite3.Row]] = {r["call_id"]: [] for r in rows}
            if rows:
                marks = ",".join("?" * len(rows))
                for turn in conn.execute(
                    f"SELECT * FROM turns WHERE call_id IN ({marks}) ORDER BY call_id, idx",
                    [r["call_id"] for r in rows],
                ):
                    turns_by_call[turn["call_id"]].append(turn)
    except Exception as exc:  # noqa: BLE001
        log.warning("history_read_failed", extra={"error": str(exc)})
        return empty

    calls = []
    for row in rows:
        valid = _valid_turns(turns_by_call[row["call_id"]])
        ttft = [t["llm_first_token_ms"] for t in valid if t["llm_first_token_ms"] is not None]
        ttfa = [t["tts_net_ttfa_ms"] for t in valid if t["tts_net_ttfa_ms"] is not None]
        minutes = row["duration_seconds"] / 60.0
        total = row["cost_total"]
        usd_inr = row["usd_inr"] or 95.0
        calls.append({
            "call_id": row["call_id"],
            "direction": row["direction"],
            "started_at": row["started_at"],
            "duration_seconds": row["duration_seconds"],
            "end_reason": row["end_reason"],
            "turns": len([t for t in turns_by_call[row["call_id"]] if t["kind"] == "response"]),
            "stack": {
                "asr_model": row["asr_model"],
                "llm_model": row["llm_model"],
                "tts_provider": row["tts_provider"],
                "tts_model": row["tts_model"],
                "voice_id": row["voice_id"],
            },
            "ttft": _call_latency(ttft),
            "ttfa": _call_latency(ttfa),
            "total": _call_latency(_turn_totals(valid)),
            "cost": total,
            "cost_inr": round(total * usd_inr, 2) if total is not None else None,
            "cost_per_minute": round(total / minutes, 6) if total is not None and minutes > 0 else None,
            "cost_per_minute_inr": (
                round(total / minutes * usd_inr, 4) if total is not None and minutes > 0 else None
            ),
        })

    all_ttft: list[float] = []
    all_ttfa: list[float] = []
    all_total: list[float] = []
    for row in rows:
        valid = _valid_turns(turns_by_call[row["call_id"]])
        all_total += _turn_totals(valid)
        all_ttft += [t["llm_first_token_ms"] for t in valid if t["llm_first_token_ms"] is not None]
        all_ttfa += [t["tts_net_ttfa_ms"] for t in valid if t["tts_net_ttfa_ms"] is not None]
    return {
        "type": kind,
        "enabled": True,
        "calls": calls,
        "summary": _summarize(rows, {"ttft": all_ttft, "ttfa": all_ttfa, "total": all_total}),
    }


def _call_latency(values: list[float]) -> dict[str, Any]:
    count = len(values)
    return {
        "count": count,
        "median": round(statistics.median(values), 1) if count >= MIN_MEDIAN_TURNS else None,
    }


def _summarize(rows: list[sqlite3.Row], latency: dict[str, list[float]]) -> dict[str, Any]:
    """Cost per minute is a ratio of sums, so longer calls weigh more."""
    priced = [r for r in rows if r["cost_total"] is not None and r["cost_asr"] is not None]
    minutes = sum(r["duration_seconds"] for r in priced) / 60.0
    usd_inr = (priced[0]["usd_inr"] if priced else None) or 95.0

    def per_minute(column: str) -> float | None:
        if not priced or minutes <= 0:
            return None
        return round(sum((r[column] or 0.0) for r in priced) / minutes, 6)

    total_pm = per_minute("cost_total")
    return {
        "calls": len(rows),
        "priced_calls": len(priced),
        "minutes": round(minutes, 2),
        "usd_inr": usd_inr,
        "cost_per_minute": {
            "total": total_pm,
            "total_inr": round(total_pm * usd_inr, 4) if total_pm is not None else None,
            "asr": per_minute("cost_asr"),
            "llm": per_minute("cost_llm"),
            "tts": per_minute("cost_tts"),
        },
        "ttft": _latency(latency.get("ttft", [])),
        "ttfa": _latency(latency.get("ttfa", [])),
        "total": _latency(latency.get("total", [])),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
