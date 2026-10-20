"""Prompt construction for the categorization agent.

All prompt text lives here — no other module should assemble LLM prompts.
The builder works with the retrieval layer's output: a shortlisted set of
candidate categories (with numeric IDs) and their supporting few-shot examples.
"""
from __future__ import annotations

from typing import Dict, List

from ..schemas import HistoricalTxn, Transaction
from .retrieval import RetrievalResult


SYSTEM_PROMPT = (
    "You are an expert bookkeeping assistant that categorizes business transactions.\n"
    "You will receive:\n"
    "  1. A numbered list of candidate categories (the ONLY valid choices).\n"
    "  2. A few labelled historical examples per category.\n"
    "  3. A new transaction to classify.\n\n"
    "Follow this decision process:\n"
    "  Step 1 — Check if the payee exactly matches a historical example. "
    "If yes, use that category.\n"
    "  Step 2 — Match the description keywords against the category examples.\n"
    "  Step 3 — If nothing fits, set category to \"Other\" (listed in candidate categories).\n\n"
    "Reasoning rules (put everything in the \"reasoning\" field only):\n"
    "  - Normal category: exactly 1 sentence, maximum 20 words.\n"
    "  - Category \"Other\": exactly 2 sentences, maximum 30 words total.\n"
    "    Sentence 1: The provided description does not match the existing descriptions "
    "of the listed categories.\n"
    "    Sentence 2: Its category should be \"<label you suggest>\".\n"
    "    The suggested label must NOT be one of the listed chart categories.\n\n"
    "Respond ONLY with a JSON object matching the required schema. "
    "Do NOT wrap it in markdown or add any text outside the JSON."
)


def build_prompt(
    txn: Transaction,
    retrieval: RetrievalResult,
    industry: str,
) -> str:
    """Assemble the user prompt from retrieval results and the new transaction."""
    categories_block = _format_categories(
        retrieval.candidate_categories,
        include_other=retrieval.used_shortlist,
    )
    examples_block = _format_examples(
        retrieval.candidate_categories,
        retrieval.examples_by_category,
    )
    chart_count = len(retrieval.candidate_categories)

    parts = [
        f"Industry: {industry}",
        "",
        "<candidate_categories>",
        categories_block,
        "</candidate_categories>",
        "",
        "<historical_examples>",
        examples_block,
        "</historical_examples>",
        "",
        "<transaction>",
        f"description: {txn.description}",
        f"payee: {txn.payee or 'N/A'}",
        "</transaction>",
        "",
        'Return JSON: {"category": "<exact name from list or Other>", '
        '"confidence": <float between 0.0 and 1.0 based on how accurate you think the detected category is>, "reasoning": "<see reasoning rules>"}',
        "",
        f"There are {chart_count} chart categories in the list above.",
    ]
    return "\n".join(parts)


def _format_categories(
    categories: List[str],
    include_other: bool = True,
) -> str:
    lines = [f"  {i}: {cat}" for i, cat in enumerate(categories)]
    if include_other and "Other" not in categories:
        lines.append(f"  {len(categories)}: Other")
    return "\n".join(lines)


def _format_examples(
    categories: List[str],
    examples_by_category: Dict[str, List[HistoricalTxn]],
) -> str:
    if not examples_by_category:
        return "(no historical examples available)"

    lines = []
    for cat in categories:
        examples = examples_by_category.get(cat, [])
        if not examples:
            continue
        lines.append(f"[{cat}]")
        for ex in examples:
            payee_part = f" | {ex.payee}" if ex.payee else ""
            lines.append(f"  - {ex.description}{payee_part}")
    return "\n".join(lines)
