from __future__ import annotations

from typing import List

from ..schemas import HistoricalTxn


# How much to bump confidence when the payee matches an existing categorized
# transaction with the same category. Small enough not to drown out the model,
# big enough to push borderline cases over the line.
EXACT_PAYEE_MATCH_BOOST = 0.1
OTHER_CATEGORY_BOOST = 0.1


def adjust_confidence(
    base: float,
    *,
    payee: str | None,
    predicted_category: str,
    history: List[HistoricalTxn],
    is_valid: bool,
) -> float:
    """Tweak the LLM's reported confidence with a couple of simple signals.

    - Bump it slightly if the payee has been seen before under the same category
      (including chart ``Other``).
    - Bump it slightly when the model selects the chart's ``Other`` category.
    - Cap it hard if the suggested category isn't in the chart of accounts.
    """
    if not is_valid:
        return min(base, 0.3)

    score = base
    if predicted_category == "Other":
        score += OTHER_CATEGORY_BOOST
    if payee:
        norm = payee.strip().lower()
        for h in history:
            if h.payee and h.payee.strip().lower() == norm and h.category == predicted_category:
                score += EXACT_PAYEE_MATCH_BOOST
                break

    return max(0.0, min(1.0, round(score, 3)))
