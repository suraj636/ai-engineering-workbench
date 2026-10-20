"""Similarity-based few-shot retrieval.

Given a new transaction and a pool of labelled historical transactions, select
the most relevant examples to include in the LLM prompt.  The pipeline:

1. Score every historical row against the query (description + payee).
2. Walk results best-first and collect distinct categories (up to MAX_CATEGORIES).
3. For each chosen category, keep the top EXAMPLES_PER_CATEGORY rows.
4. Return the shortlisted categories and their supporting examples.

No external dependencies — uses stdlib ``difflib.SequenceMatcher``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

from ..schemas import HistoricalTxn


MAX_CATEGORIES = 5
EXAMPLES_PER_CATEGORY = 2
SIMILARITY_THRESHOLD = 0.05


@dataclass
class RetrievalResult:
    """Container returned by ``select_few_shot``."""
    candidate_categories: List[str]
    examples_by_category: Dict[str, List[HistoricalTxn]] = field(default_factory=dict)
    used_shortlist: bool = True


def _normalize_text(description: str, payee: Optional[str] = None) -> str:
    parts = [description]
    if payee:
        parts.append(payee)
    return " ".join(parts).lower().strip()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _score_history(
    query: str,
    history: List[HistoricalTxn],
) -> List[Tuple[float, int, HistoricalTxn]]:
    """Return (score, original_index, txn) sorted descending by score, then by
    original index for deterministic tie-breaking."""
    scored = []
    for idx, h in enumerate(history):
        h_text = _normalize_text(h.description, h.payee)
        score = _similarity(query, h_text)
        scored.append((score, idx, h))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return scored


def select_few_shot(
    description: str,
    payee: Optional[str],
    history: List[HistoricalTxn],
    chart_of_accounts: List[str],
    *,
    max_categories: int = MAX_CATEGORIES,
    examples_per_category: int = EXAMPLES_PER_CATEGORY,
) -> RetrievalResult:
    """Pick the most relevant few-shot examples for the LLM prompt.

    If the chart is small enough (<= max_categories + 2) or history is empty,
    falls back to sending the full chart with best available examples.
    """
    if not history:
        return RetrievalResult(
            candidate_categories=list(chart_of_accounts),
            examples_by_category={},
            used_shortlist=False,
        )

    query = _normalize_text(description, payee)
    scored = _score_history(query, history)

    if len(chart_of_accounts) <= max_categories + 2:
        return _full_chart_result(
            chart_of_accounts, scored, examples_per_category,
        )

    best_score = scored[0][0] if scored else 0.0
    if best_score < SIMILARITY_THRESHOLD:
        return _full_chart_result(
            chart_of_accounts, scored, examples_per_category,
        )

    # --- Exact-payee pinning: force that category to rank 1 ---
    pinned_category: Optional[str] = None
    if payee:
        norm_payee = payee.strip().lower()
        for _, _, h in scored:
            if h.payee and h.payee.strip().lower() == norm_payee:
                pinned_category = h.category
                break

    # --- Walk scored list, collect distinct categories up to limit ---
    categories_ordered: List[str] = []
    if pinned_category and pinned_category in chart_of_accounts:
        categories_ordered.append(pinned_category)

    for _, _, h in scored:
        if h.category in categories_ordered:
            continue
        if h.category not in chart_of_accounts:
            continue
        categories_ordered.append(h.category)
        if len(categories_ordered) >= max_categories:
            break

    # --- Fill examples per category ---
    examples_by_cat: Dict[str, List[HistoricalTxn]] = {}
    for cat in categories_ordered:
        cat_examples = []
        for _, _, h in scored:
            if h.category == cat:
                cat_examples.append(h)
                if len(cat_examples) >= examples_per_category:
                    break
        examples_by_cat[cat] = cat_examples

    if "Other" in chart_of_accounts and "Other" not in categories_ordered:
        categories_ordered.append("Other")
        examples_by_cat.setdefault("Other", [])

    return RetrievalResult(
        candidate_categories=categories_ordered,
        examples_by_category=examples_by_cat,
        used_shortlist=True,
    )


def _full_chart_result(
    chart: List[str],
    scored: List[Tuple[float, int, HistoricalTxn]],
    examples_per_category: int,
) -> RetrievalResult:
    """Fallback: use all chart categories with best-matching examples."""
    examples_by_cat: Dict[str, List[HistoricalTxn]] = {}
    for cat in chart:
        cat_examples = []
        for _, _, h in scored:
            if h.category == cat:
                cat_examples.append(h)
                if len(cat_examples) >= examples_per_category:
                    break
        if cat_examples:
            examples_by_cat[cat] = cat_examples

    return RetrievalResult(
        candidate_categories=list(chart),
        examples_by_category=examples_by_cat,
        used_shortlist=False,
    )
