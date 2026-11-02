"""Dashboard API: health, runtime model config, usage, costs and hangup."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend import __version__, call_db
from backend.config import (
    ASR_MODELS,
    INWORLD_MODELS,
    INWORLD_VOICE_CATALOG,
    INWORLD_VOICE_LABELS,
    INWORLD_VOICES,
    SARVAM_MODELS,
    SARVAM_TTS_MODELS,
    SARVAM_VOICE_LABELS,
    SARVAM_VOICES,
    SPEECHIFY_MODELS,
    SPEECHIFY_VOICE_CATALOG,
    SPEECHIFY_VOICE_LABELS,
    SPEECHIFY_VOICES,
    TTS_PROVIDERS,
    runtime_config,
    settings,
)
from backend.services.network_check import network_checker
from backend.services.tts_factory import TTS_PROVIDER_LABELS
from backend.session_store import registry
from backend.services.warmup import warmup_manager

log = logging.getLogger("api_routes")
router = APIRouter(prefix="/api", tags=["api"])


@router.get("/health")
async def health() -> dict:
    missing = runtime_config.missing_credentials
    return {
        "status": "ok" if not missing else "degraded",
        "version": __version__,
        "providers": {
            "plivo": settings.plivo_configured,
            "assemblyai": settings.assemblyai_configured,
            "sarvam": settings.sarvam_configured,
            "inworld": settings.inworld_configured,
            "speechify": settings.speechify_configured,
            "sarvam_tts": settings.sarvam_configured,
        },
        "tts_provider": runtime_config.tts_provider,
        "voice_pipeline_ready": runtime_config.voice_pipeline_ready,
        "missing_credentials": missing,
        "public_base_url": settings.public_base_url,
        "plivo_number": settings.plivo_phone_number or None,
        "pricing_configured": settings.pricing_configured(),
        "active_calls": len(registry.active),
        "warmup": warmup_manager.snapshot(),
        "models": {
            "asr": runtime_config.asr_speech_model,
            "llm": runtime_config.sarvam_model,
            "tts": runtime_config.tts_model,
            "tts_provider": runtime_config.tts_provider,
            "tts_label": TTS_PROVIDER_LABELS.get(
                runtime_config.tts_provider, runtime_config.tts_provider
            ),
        },
    }


@router.get("/warmup")
async def warmup_status() -> dict:
    return warmup_manager.snapshot()


@router.post("/warmup")
async def warmup_rerun() -> dict:
    """Kick off a fresh warm-up in the background and return the snapshot."""
    warmup_manager.start(force=True)
    return warmup_manager.snapshot()


@router.get("/network-check")
async def network_check_status() -> dict:
    """Latest network check result (null until one has run)."""
    return network_checker.snapshot()


@router.post("/network-check")
async def network_check_run() -> dict:
    """Measure DNS/TCP/TLS to every provider; no API keys or credits are used."""
    return await network_checker.run()


@router.get("/config")
async def get_config() -> dict:
    active_provider = runtime_config.tts_provider
    active_voices = {
        "speechify": SPEECHIFY_VOICES,
        "sarvam": SARVAM_VOICES,
    }.get(active_provider, INWORLD_VOICES)
    return {
        "config": runtime_config.public(),
        "options": {
            "asr_models": ASR_MODELS,
            "sarvam_models": SARVAM_MODELS,
            "tts_providers": TTS_PROVIDERS,
            "tts_provider_labels": TTS_PROVIDER_LABELS,
            "inworld_models": INWORLD_MODELS,
            "speechify_models": SPEECHIFY_MODELS,
            "inworld_voices": INWORLD_VOICES,
            "inworld_voice_catalog": INWORLD_VOICE_CATALOG,
            "inworld_voice_labels": INWORLD_VOICE_LABELS,
            "speechify_voices": SPEECHIFY_VOICES,
            "speechify_voice_catalog": SPEECHIFY_VOICE_CATALOG,
            "speechify_voice_labels": SPEECHIFY_VOICE_LABELS,
            "sarvam_tts_models": SARVAM_TTS_MODELS,
            "sarvam_tts_voices": SARVAM_VOICES,
            "sarvam_tts_voice_labels": SARVAM_VOICE_LABELS,
            "voices": active_voices,
        },
        "providers": {
            "plivo": settings.plivo_configured,
            "assemblyai": settings.assemblyai_configured,
            "sarvam": settings.sarvam_configured,
            "inworld": settings.inworld_configured,
            "speechify": settings.speechify_configured,
            "sarvam_tts": settings.sarvam_configured,
        },
        "missing_credentials": runtime_config.missing_credentials,
        "pricing": settings.pricing(),
        "pricing_configured": settings.pricing_configured(),
    }


@router.post("/config")
async def update_config(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Expected JSON body")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object")
    allowed = {
        "tts_provider",
        "asr_speech_model",
        "sarvam_model",
        "inworld_model",
        "speechify_model",
        "sarvam_tts_model",
        "voice_id",
        "temperature",
        "max_tokens",
        "greeting",
        "speaking_rate",
        "delivery_mode",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown fields: {sorted(unknown)}")
    try:
        updated = runtime_config.apply(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("runtime_config_updated", extra={"fields": sorted(payload)})
    return JSONResponse({"ok": True, "config": updated})


@router.get("/stats")
async def stats() -> dict:
    return registry.stats(settings)


@router.get("/calls")
async def calls() -> dict:
    data = registry.stats(settings)
    return {"calls": data["calls"], "totals": data["totals"], "costs": data["costs"]}


@router.get("/calls/{call_id}")
async def call_detail(call_id: str) -> dict:
    record = registry.get(call_id)
    if record is not None:
        return record.to_dict(settings)
    # Not in memory (e.g. the server restarted since): fall back to saved history.
    saved = await asyncio.to_thread(call_db.get_call, call_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Call not found")
    return saved


@router.get("/history")
async def history(type: str = "all") -> dict:
    """Last 10 calls per type (or of one type) with median/p95 latency and cost per minute."""
    if type not in {"all", "inbound", "outbound", "web"}:
        raise HTTPException(status_code=400, detail="type must be all, inbound, outbound or web")
    return await asyncio.to_thread(call_db.history, type)


@router.post("/calls/{call_id}/hangup")
async def hangup_call(call_id: str) -> JSONResponse:
    agent = registry.active.get(call_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Call is not active")
    await agent.end("user_requested", hangup=True)
    return JSONResponse({"ok": True, "call_id": call_id})


@router.get("/pricing")
async def pricing() -> dict:
    return {
        "rates": settings.pricing(),
        "configured": settings.pricing_configured(),
        "note": (
            "Static list prices are captured in backend/pricing.py (as of "
            "2026-10-29): AssemblyAI $0.45/hour billed per session minute, "
            "Sarvam 105B output converted to a per-character estimate, and "
            "Speechify Simba 3.5 at $10/1M characters, and Sarvam Bulbul v4 "
            "Flash at INR 1,000/1M characters. Override with PRICING_* "
            "environment variables if your contract differs."
        ),
    }
