from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .base import LLMClient, LLMResponse


_KEYWORDS = [
    ("Software Subscriptions", ["aws", "google workspace", "figma", "notion", "github", "saas", "subscription", "slack", "jira", "zoom", "dropbox", "salesforce", "crm"]),
    ("Travel & Meals",         ["uber", "lyft", "airline", "hotel", "restaurant", "lunch", "dinner", "coffee", "flight", "rental car", "conference", "travel", "catering", "party", "reimbursement"]),
    ("Marketing & Advertising",["facebook ads", "google ads", "meta", "linkedin ads", "campaign", "seo", "trade show", "swag", "mailchimp", "pr agency", "webinar", "podcast", "sponsorship"]),
    ("Professional Services",  ["legal", "lawyer", "consultant", "consulting", "accountant", "audit", "tax", "bookkeeping", "recruitment", "insurance broker", "advisory"]),
    ("Office Supplies",        ["staples", "paper", "printer", "stationery", "office depot", "toner", "cartridge", "keyboard", "desk", "monitor", "cable"]),
    ("Utilities",              ["electric", "water", "internet", "comcast", "verizon", "phone plan", "gas", "heating", "waste", "alarm", "janitorial", "hvac"]),
    ("Payroll",                ["salary", "salaries", "payroll", "401k", "health insurance", "bonus", "contractor payment", "stock option", "workers comp"]),
    ("Bank Fees",              ["wire fee", "bank fee", "overdraft", "transfer fee", "ach", "processing fee", "merchant", "gateway fee", "payment fee", "credit card processing"]),
]


class MockLLMClient(LLMClient):
    """Deterministic fake. Used in tests and as a fallback when no API key is set."""

    def __init__(self, model: str = "mock-llm-v1"):
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: Dict[str, Any],
    ) -> LLMResponse:
        text_blob = user_prompt.lower()
        allowed = self._extract_allowed(user_prompt)
        guess, hit = self._best_match(text_blob, allowed)

        payload = {
            "category": guess,
            "confidence": 0.85 if hit else 0.4,
            "reasoning": (
                f"Matched keyword '{hit}' in transaction text." if hit
                else "No strong keyword match; falling back to first available category."
            ),
        }
        return LLMResponse(text=json.dumps(payload), model=self._model)

    @staticmethod
    def _extract_allowed(prompt: str) -> List[str]:
        m = re.search(r"<candidate_categories>(.*?)</candidate_categories>", prompt, re.DOTALL)
        if not m:
            m = re.search(r"<chart_of_accounts>(.*?)</chart_of_accounts>", prompt, re.DOTALL)
        if not m:
            return []
        lines = []
        for line in m.group(1).splitlines():
            line = line.strip()
            if not line:
                continue
            cleaned = re.sub(r"^\d+:\s*", "", line).strip("- ").strip()
            if cleaned and cleaned != "Other":
                lines.append(cleaned)
        return lines

    @staticmethod
    def _best_match(text: str, allowed: List[str]) -> tuple[str, Optional[str]]:
        for category, keywords in _KEYWORDS:
            if category not in allowed:
                continue
            for kw in keywords:
                if kw in text:
                    return category, kw
        return (allowed[0] if allowed else "Uncategorized"), None
