from __future__ import annotations

import json
import logging
from typing import Any, Dict

from .base import LLMClient, LLMError, LLMResponse

logger = logging.getLogger(__name__)


class GroqClient(LLMClient):
    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile"):
        if not api_key:
            raise LLMError("GROQ_API_KEY is not set")

        from groq import Groq

        self._client = Groq(api_key=api_key)
        self._model_name = model

    @property
    def model_name(self) -> str:
        return self._model_name

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: Dict[str, Any],
    ) -> LLMResponse:
        schema_hint = (
            "\n\nYour response MUST be a single JSON object matching this schema "
            "(no extra fields, no prose):\n"
            f"{json.dumps(response_schema)}"
        )

        try:
            resp = self._client.chat.completions.create(
                model=self._model_name,
                temperature=0.0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt + schema_hint},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception as exc:
            logger.exception("Groq call failed")
            raise LLMError(f"Groq request failed: {exc}") from exc

        if not resp.choices or not resp.choices[0].message.content:
            raise LLMError("Groq returned an empty response")

        return LLMResponse(
            text=resp.choices[0].message.content.strip(),
            model=self._model_name,
            raw=resp,
        )
