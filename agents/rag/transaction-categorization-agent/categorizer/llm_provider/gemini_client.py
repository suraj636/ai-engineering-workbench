from __future__ import annotations

import logging
from typing import Any, Dict

from .base import LLMClient, LLMError, LLMResponse

logger = logging.getLogger(__name__)


class GeminiClient(LLMClient):
    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        if not api_key:
            raise LLMError("GEMINI_API_KEY is not set")

        import google.generativeai as genai

        genai.configure(api_key=api_key)
        self._genai = genai
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
        model = self._genai.GenerativeModel(
            model_name=self._model_name,
            system_instruction=system_prompt,
        )

        try:
            resp = model.generate_content(
                user_prompt,
                generation_config={
                    "temperature": 0.0,
                    "response_mime_type": "application/json",
                    "response_schema": response_schema,
                },
            )
        except Exception as exc:
            logger.exception("Gemini call failed")
            raise LLMError(f"Gemini request failed: {exc}") from exc

        text = (resp.text or "").strip()
        if not text:
            raise LLMError("Gemini returned an empty response")

        return LLMResponse(text=text, model=self._model_name, raw=resp)
