from __future__ import annotations

import logging

from django.conf import settings

from .base import LLMClient
from .gemini_client import GeminiClient
from .groq_client import GroqClient
from .mock_client import MockLLMClient

logger = logging.getLogger(__name__)


def get_llm_client() -> LLMClient:
    """Pick a client based on settings. Falls back to mock if API key is missing."""
    cfg = settings.LLM
    provider = cfg.get("provider", "gemini")

    if provider == "mock":
        return MockLLMClient()

    if provider == "gemini":
        gem = cfg["gemini"]
        if not gem.get("api_key"):
            logger.warning("GEMINI_API_KEY missing — falling back to MockLLMClient")
            return MockLLMClient()
        return GeminiClient(api_key=gem["api_key"], model=gem["model"])

    if provider == "groq":
        groq = cfg["groq"]
        if not groq.get("api_key"):
            logger.warning("GROQ_API_KEY missing — falling back to MockLLMClient")
            return MockLLMClient()
        return GroqClient(api_key=groq["api_key"], model=groq["model"])

    raise ValueError(f"Unknown LLM provider: {provider!r}")
