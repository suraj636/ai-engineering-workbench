from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class LLMResponse:
    text: str
    model: str
    raw: Optional[Any] = None


class LLMError(Exception):
    """Raised when an LLM call fails or returns something unusable."""


class LLMClient(ABC):
    """Provider-agnostic interface. Keep this minimal — swap-friendly is the point."""

    @abstractmethod
    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: Dict[str, Any],
    ) -> LLMResponse:
        """Run a single completion that must return JSON matching response_schema."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        ...
