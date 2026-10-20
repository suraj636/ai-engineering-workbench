"""Parse and validate LLM JSON responses.

Responsibilities:
- Extract valid JSON from the LLM output (with a salvage fallback for
  responses wrapped in prose).
- Validate the parsed dict against the ``LLMSuggestion`` Pydantic model.
- Check whether the returned category is in the chart of accounts.
"""
from __future__ import annotations

import json
import logging
from typing import List, Optional

from pydantic import ValidationError

from ..schemas import LLMSuggestion

logger = logging.getLogger(__name__)


class ParseError(Exception):
    pass


def parse_suggestion(raw_text: str) -> LLMSuggestion:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        data = _salvage_json(raw_text)
        if data is None:
            raise ParseError(f"Could not parse JSON from LLM output: {e}") from e

    try:
        return LLMSuggestion(**data)
    except ValidationError as e:
        raise ParseError(f"LLM output didn't match expected schema: {e}") from e


def _salvage_json(text: str) -> Optional[dict]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def category_is_valid(category: str, chart_of_accounts: List[str]) -> bool:
    return category in chart_of_accounts
