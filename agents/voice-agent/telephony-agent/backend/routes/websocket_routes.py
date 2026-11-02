"""Bidirectional audio WebSockets: Plivo media, browser test calls, UI events."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.agents.voice_agent import BrowserTransport, PlivoTransport, VoiceAgent
from backend.config import runtime_config, settings
from backend.services.warmup import warmup_manager
from backend.session_store import event_bus, registry, utc_now_iso

log = logging.getLogger("websocket_routes")
router = APIRouter(tags=["websocket"])


@router.websocket("/ws/plivo")
async def plivo_stream(
    websocket: WebSocket,
    direction: str = "inbound",
    from_number: str = "",
    to_number: str = "",
) -> None:
    await websocket.accept()
    agent: VoiceAgent | None = None
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=15)
        event = json.loads(raw)
        if event.get("event") != "start":
            log.warning("plivo_ws_unexpected_first_event", extra={"event": event.get("event")})
            await websocket.close(code=1002)
            return

        start = event.get("start") or {}
        call_id = str(start.get("callId") or f"plivo_{uuid.uuid4().hex[:12]}")
        stream_id = str(start.get("streamId") or "")
        media_format = start.get("mediaFormat") or {}
        log.info(
            "plivo_ws_start",
            extra={
                "call_id": call_id,
                "stream_id": stream_id,
                "encoding": media_format.get("encoding"),
                "sample_rate": media_format.get("sampleRate"),
                "direction": direction,
            },
        )

        record = registry.create(
            direction,
            from_number=from_number,
            to_number=to_number,
            voice_id=runtime_config.voice_id,
            tts_provider=runtime_config.tts_provider,
            tts_model=runtime_config.tts_model,
            call_id=call_id,
        )
        agent = VoiceAgent(
            call_id=record.call_id,
            direction=direction,
            transport=PlivoTransport(websocket, stream_id),
            record=record,
            config=runtime_config,
            settings=settings,
            from_number=from_number,
            to_number=to_number,
        )
        registry.attach_agent(record.call_id, agent)
        await agent.start()

        while True:
            raw = await websocket.receive_text()
            try:
                event = json.loads(raw)
            except (TypeError, ValueError):
                continue
            event_type = event.get("event")
            if event_type == "media":
                media = event.get("media") or {}
                if media.get("track") not in (None, "inbound"):
                    continue
                payload = media.get("payload")
                if payload:
                    try:
                        await agent.send_caller_audio(base64.b64decode(payload))
                    except Exception:
                        log.exception("plivo_media_decode_failed")
            elif event_type == "playedStream":
                await agent.on_played_stream(str(event.get("name") or ""))
            elif event_type == "clearedAudio":
                log.debug("plivo_cleared_audio", extra={"call_id": call_id})
            elif event_type == "dtmf":
                digit = (event.get("dtmf") or {}).get("digit")
                event_bus.publish(
                    {"type": "dtmf", "call_id": call_id, "digit": digit, "at": utc_now_iso()}
                )
            elif event_type in {"stop", "closed"}:
                break
    except WebSocketDisconnect:
        log.info("plivo_ws_disconnected", extra={"call_id": agent.call_id if agent else None})
    except asyncio.TimeoutError:
        log.warning("plivo_ws_start_timeout")
    except Exception as exc:  # noqa: BLE001 - connection boundary
        log.exception("plivo_ws_error", extra={"error": str(exc)})
    finally:
        if agent is not None:
            await agent.end("caller_hung_up")
        else:
            try:
                await websocket.close()
            except Exception:
                pass


@router.websocket("/ws/browser")
async def browser_call(websocket: WebSocket) -> None:
    await websocket.accept()
    agent: VoiceAgent | None = None
    try:
        if not runtime_config.voice_pipeline_ready:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "Missing credentials: "
                    + ", ".join(runtime_config.missing_credentials),
                }
            )
            await websocket.close(code=1011)
            return

        first = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        start = json.loads(first)
        if start.get("type") != "start":
            await websocket.send_json({"type": "error", "message": "Expected a start message"})
            await websocket.close(code=1002)
            return

        record = registry.create(
            "browser",
            from_number="browser",
            to_number=str(start.get("dial") or "browser"),
            voice_id=runtime_config.voice_id,
            tts_provider=runtime_config.tts_provider,
            tts_model=runtime_config.tts_model,
        )
        agent = VoiceAgent(
            call_id=record.call_id,
            direction="browser",
            transport=BrowserTransport(websocket),
            record=record,
            config=runtime_config,
            settings=settings,
            from_number="browser",
            to_number=record.to_number,
        )
        registry.attach_agent(record.call_id, agent)
        await websocket.send_json(
            {
                "type": "ready",
                "call_id": record.call_id,
                "voice_id": runtime_config.voice_id,
                "tts_provider": runtime_config.tts_provider,
                "tts_model": runtime_config.tts_model,
                "sample_rate": 8000,
            }
        )
        await agent.start()

        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            kind = message.get("type")
            if kind == "audio":
                payload = message.get("payload")
                if payload:
                    await agent.send_caller_audio(base64.b64decode(payload))
            elif kind == "stop":
                break
            elif kind == "ping":
                await websocket.send_json({"type": "pong", "ts": utc_now_iso()})
    except WebSocketDisconnect:
        log.info("browser_ws_disconnected", extra={"call_id": agent.call_id if agent else None})
    except asyncio.TimeoutError:
        log.warning("browser_ws_start_timeout")
    except Exception as exc:  # noqa: BLE001 - connection boundary
        log.exception("browser_ws_error", extra={"error": str(exc)})
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        if agent is not None:
            await agent.end("browser_hung_up")
        else:
            try:
                await websocket.close()
            except Exception:
                pass


@router.websocket("/ws/events")
async def dashboard_events(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = event_bus.subscribe()
    try:
        await websocket.send_json(
            {
                "type": "hello",
                "active_calls": len(registry.active),
                "warmup": warmup_manager.snapshot(),
                "at": utc_now_iso(),
            }
        )
        while True:
            try:
                event: dict[str, Any] = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping", "at": utc_now_iso()})
                continue
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - connection boundary
        log.debug("dashboard_ws_closed")
    finally:
        event_bus.unsubscribe(queue)
