"""Plivo webhooks: inbound answer XML, outbound call creation, status callbacks."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from backend.config import settings
from backend.services.plivo_service import (
    build_stream_xml,
    plivo_service,
    verify_signature,
)
from backend.session_store import event_bus

log = logging.getLogger("plivo_routes")
router = APIRouter(prefix="/plivo", tags=["plivo"])
PHONE_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def _base_url(request: Request) -> str:
    return settings.public_base(str(request.base_url))


def _ws_url(request: Request, direction: str, from_number: str, to_number: str) -> str:
    query = urlencode(
        {"direction": direction, "from_number": from_number, "to_number": to_number}
    )
    return f"{settings.ws_base(str(request.base_url))}/ws/plivo?{query}"


async def _check_signature(request: Request) -> None:
    if not settings.plivo_validate_signature:
        return
    if not settings.plivo_auth_token:
        raise HTTPException(status_code=500, detail="Plivo auth token not configured")
    url = _base_url(request) + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    valid = verify_signature(
        method=request.method,
        url=url,
        nonce=request.headers.get("x-plivo-signature-v3-nonce"),
        signature=request.headers.get("x-plivo-signature-v3"),
        auth_token=settings.plivo_auth_token,
    )
    if not valid:
        log.warning("plivo_signature_invalid", extra={"path": request.url.path})
        raise HTTPException(status_code=403, detail="Invalid Plivo signature")


async def _answer_xml(request: Request, direction: str) -> Response:
    await _check_signature(request)
    form = await request.form()
    from_number = str(form.get("From") or form.get("from") or "")
    to_number = str(form.get("To") or form.get("to") or "")
    ws_url = _ws_url(request, direction, from_number, to_number)
    status_callback = f"{_base_url(request)}/plivo/status"
    xml = build_stream_xml(
        ws_url=ws_url,
        status_callback_url=status_callback,
        direction=direction,
    )
    log.info(
        "plivo_answer_xml",
        extra={"direction": direction, "from": from_number, "to": to_number},
    )
    if not settings.public_base_url:
        log.warning(
            "public_base_url_missing",
            extra={"hint": "Set PUBLIC_BASE_URL to your HTTPS origin for real calls"},
        )
    return Response(content=xml, media_type="application/xml")


@router.api_route("/inbound", methods=["GET", "POST"])
async def inbound_call(request: Request) -> Response:
    return await _answer_xml(request, "inbound")


@router.api_route("/answer", methods=["GET", "POST"])
async def outbound_answer(request: Request) -> Response:
    direction = request.query_params.get("direction")
    if not direction:
        # No explicit direction in the URL: trust the Direction Plivo sends, so a
        # number whose application uses /plivo/answer is not tagged "outbound".
        form = await request.form()
        sent = str(form.get("Direction") or "").lower()
        direction = sent if sent in ("inbound", "outbound") else "outbound"
    return await _answer_xml(request, direction)


@router.post("/outbound")
async def create_outbound(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Expected JSON body")
    to_number = str(payload.get("to") or "").strip().replace(" ", "").replace("-", "")
    if not PHONE_RE.match(to_number):
        raise HTTPException(
            status_code=400,
            detail="Destination must be E.164 format, for example +14155550123",
        )
    try:
        result = await plivo_service.create_outbound_call(to_number)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - provider boundary
        log.exception("plivo_outbound_failed")
        raise HTTPException(status_code=502, detail=f"Plivo call failed: {exc}") from exc
    return JSONResponse({"ok": True, "call": result, "to": to_number})


@router.api_route("/status", methods=["GET", "POST"])
async def status_callback(request: Request) -> JSONResponse:
    form = await request.form()
    data = {key: str(value) for key, value in form.items()}
    log.info("plivo_status", extra={"status": data.get("CallStatus"), "event": data.get("Event")})
    event_bus.publish(
        {
            "type": "plivo_status",
            "call_id": data.get("CallUUID") or "",
            "call_status": data.get("CallStatus") or "",
            "event": data.get("Event") or "",
            "direction": data.get("Direction") or "",
            "from_number": data.get("From") or "",
            "to_number": data.get("To") or "",
            "duration": data.get("Duration"),
        }
    )
    return JSONResponse({"ok": True})
