"""Orchestrator: the single place where retrieval, prompting, LLM invocation,
parsing, and confidence calibration meet.

Flow:
  1. Retrieve relevant few-shot examples (similarity-based shortlisting).
  2. Build a structured prompt with candidate category IDs.
  3. Invoke the LLM.
  4. Parse the JSON response.
  5. Validate category against chart of accounts.
  6. Adjust confidence with heuristics.
  7. Return a validated CategorizationResult.
"""
from __future__ import annotations

import logging

from ..llm_provider.base import LLMClient, LLMError
from ..llm_provider.factory import get_llm_client
from ..schemas import CategorizationResult, CompanyContext, Transaction
from . import context_builder, response_parser
from .confidence import adjust_confidence
from .retrieval import select_few_shot

logger = logging.getLogger(__name__)

REASONING_MAX_WORDS = 20
OTHER_REASONING_MAX_WORDS = 30

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string"},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["category", "confidence", "reasoning"],
}


def _trim_reasoning(text: str | None, max_words: int) -> str | None:
    if not text:
        return None
    words = text.split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words]).strip()


class CategorizerService:
    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm or get_llm_client()

    def categorize(self, txn: Transaction, ctx: CompanyContext) -> CategorizationResult:
        logger.info(
            "categorize: company=%s industry=%s history=%d accounts=%d",
            ctx.company_id, ctx.industry,
            len(ctx.historical_transactions), len(ctx.chart_of_accounts),
        )

        retrieval = select_few_shot(
            description=txn.description,
            payee=txn.payee,
            history=ctx.historical_transactions,
            chart_of_accounts=ctx.chart_of_accounts,
        )

        logger.info(
            "retrieval: shortlist=%s categories=%d examples=%d",
            retrieval.used_shortlist,
            len(retrieval.candidate_categories),
            sum(len(v) for v in retrieval.examples_by_category.values()),
        )

        prompt = context_builder.build_prompt(txn, retrieval, ctx.industry)
        try:
            llm_resp = self._llm.complete_json(
                system_prompt=context_builder.SYSTEM_PROMPT,
                user_prompt=prompt,
                response_schema=_RESPONSE_SCHEMA,
            )
        except LLMError:
            raise

        suggestion = response_parser.parse_suggestion(llm_resp.text)

        if response_parser.category_is_valid(
            suggestion.category, ctx.chart_of_accounts,
        ):
            is_valid = True
            final_category = suggestion.category
            max_words = (
                OTHER_REASONING_MAX_WORDS
                if suggestion.category == "Other"
                else REASONING_MAX_WORDS
            )
            reasoning = _trim_reasoning(suggestion.reasoning, max_words)
        else:
            is_valid = False
            final_category = "Uncategorized"
            reasoning = _trim_reasoning(suggestion.reasoning, REASONING_MAX_WORDS)

        confidence = adjust_confidence(
            suggestion.confidence,
            payee=txn.payee,
            predicted_category=suggestion.category,
            history=ctx.historical_transactions,
            is_valid=is_valid,
        )

        return CategorizationResult(
            category=final_category,
            confidence=confidence,
            reasoning=reasoning,
            is_valid_category=is_valid,
            model=llm_resp.model,
        )
