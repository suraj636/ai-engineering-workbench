"""Plivo REST helpers, answer XML and webhook signature validation."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import plivo

from backend.config import settings

log = logging.getLogger("plivo_service")

STREAM_CONTENT_TYPE = "audio/x-mulaw;rate=8000"


class PlivoService:
    def __init__(self, settings) -> None:
        self.settings = settings
        self._client: plivo.RestClient | None = None

    def _get_client(self) -> plivo.RestClient:
        if self._client is None:
            self._client = plivo.RestClient(
                auth_id=self.settings.plivo_auth_id,
                auth_token=self.settings.plivo_auth_token,
            )
        return self._client

    async def create_outbound_call(self, to_number: str) -> dict[str, Any]:
        if not self.settings.plivo_configured:
            raise RuntimeError(
                "Plivo is not configured. Set PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN and "
                "PLIVO_PHONE_NUMBER."
            )
        if not self.settings.public_base_url:
            raise RuntimeError(
                "PUBLIC_BASE_URL is required for outbound calls (your ngrok/HTTPS origin)."
            )
        answer_url = f"{self.settings.public_base_url}/plivo/answer?direction=outbound"
        hangup_url = f"{self.settings.public_base_url}/plivo/status"
        return await asyncio.to_thread(
            self._create_call, to_number, answer_url, hangup_url
        )

    async def check_account(self) -> dict[str, Any]:
        """Validate REST credentials without placing a call (warm-up)."""
        if not (self.settings.plivo_auth_id and self.settings.plivo_auth_token):
            raise RuntimeError("Plivo credentials are not configured")
        return await asyncio.to_thread(self._check_account)

    def _check_account(self) -> dict[str, Any]:
        account = self._get_client().account.get()
        name = getattr(account, "name", None) or getattr(account, "account_name", None)
        account_type = getattr(account, "account_type", None)
        return {
            "name": str(name) if name else "",
            "account_type": str(account_type) if account_type else "",
        }

    def _create_call(self, to_number: str, answer_url: str, hangup_url: str) -> dict[str, Any]:
        client = self._get_client()
        response = client.calls.create(
            from_=self.settings.plivo_phone_number,
            to_=to_number,
            answer_url=answer_url,
            answer_method="POST",
            hangup_url=hangup_url,
            hangup_method="POST",
        )
        payload = _response_to_dict(response)
        log.info(
            "plivo_outbound_created",
            extra={"to": to_number, "call_uuid": payload.get("call_uuid")},
        )
        return payload

    async def hangup(self, call_uuid: str) -> bool:
        if not call_uuid or not self.settings.plivo_configured:
            return False
        try:
            await asyncio.to_thread(self._hangup, call_uuid)
            log.info("plivo_hangup", extra={"call_uuid": call_uuid})
            return True
        except Exception as exc:  # noqa: BLE001 - provider boundary
            log.warning("plivo_hangup_failed", extra={"call_uuid": call_uuid, "error": str(exc)})
            return False

    def _hangup(self, call_uuid: str) -> None:
        self._get_client().calls.hangup(call_uuid)


def _response_to_dict(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    data: dict[str, Any] = {}
    for key in ("call_uuid", "request_uuid", "message", "api_id"):
        value = getattr(response, key, None)
        if value is not None:
            data[key] = str(value)
    return data


def build_stream_xml(
    *,
    ws_url: str,
    status_callback_url: str,
    direction: str,
) -> str:
    """Bidirectional, keep-alive media stream in Plivo's telephony format."""
    callback = quoteattr(status_callback_url) if status_callback_url else '""'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        "  <Stream"
        ' bidirectional="true"'
        ' keepCallAlive="true"'
        f' contentType="{STREAM_CONTENT_TYPE}"'
        f" statusCallbackUrl={callback}"
        ">"
        f"{escape(ws_url)}"
        "</Stream>\n"
        "</Response>"
    )


def verify_signature(
    *,
    method: str,
    url: str,
    nonce: str | None,
    signature: str | None,
    auth_token: str,
) -> bool:
    """Validate Plivo's X-Plivo-Signature-V3 HMAC (SHA-256, base64)."""
    if not nonce or not signature:
        return False
    base_string = f"{method.upper()}{url}{nonce}"
    digest = hmac.new(
        auth_token.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha256
    ).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, signature)


plivo_service = PlivoService(settings)
