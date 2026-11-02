"""Sarvam-105B chat completions with SSE streaming.

Endpoint: POST https://api.sarvam.ai/v1/chat/completions
Auth:     `api-subscription-key: <key>` (Bearer is also accepted by Sarvam)
Notes:    thinking mode is ON by default, so `reasoning_effort: null` is sent
          explicitly for voice latency. The final SSE chunk carries `usage`.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncIterator, Callable

import httpx

log = logging.getLogger("sarvam_llm")


class SarvamError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SarvamLLM:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.sarvam.ai",
        model: str = "sarvam-105b",
        temperature: float = 0.6,
        max_tokens: int = 300,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=10.0),
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            )
        return self._client

    async def stream_chat(
        self,
        messages: list[dict[str, str]],
        *,
        on_usage: Callable[[dict[str, Any]], None] | None = None,
    ) -> AsyncIterator[str]:
        """Yield content deltas from a streaming completion."""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            # Voice agents should not pay for (or wait on) thinking tokens.
            "reasoning_effort": None,
        }
        headers = {
            "api-subscription-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        url = f"{self.base_url}/v1/chat/completions"
        started = time.perf_counter()
        first_token_logged = False

        async with self._get_client().stream(
            "POST", url, json=payload, headers=headers
        ) as response:
            headers_ms = (time.perf_counter() - started) * 1000
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")[:500]
                raise SarvamError(
                    f"Sarvam returned HTTP {response.status_code}: {body}",
                    status_code=response.status_code,
                )

            async for line in response.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue

                usage = event.get("usage")
                if usage and on_usage:
                    try:
                        on_usage(usage)
                    except Exception:
                        log.exception("sarvam_usage_callback_failed")

                choices = event.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    if not first_token_logged:
                        first_token_logged = True
                        # headers_ms separates connect/queueing from generation.
                        log.info(
                            "sarvam_first_token",
                            extra={
                                "headers_ms": round(headers_ms, 1),
                                "first_token_ms": round(
                                    (time.perf_counter() - started) * 1000, 1
                                ),
                                "token": content[:20],
                            },
                        )
                    yield content

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None
