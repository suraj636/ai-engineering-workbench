"""Evaluation harness: run each sample through the categorizer and report metrics.

Metrics reported:
  - Overall accuracy (exact category match)
  - Average confidence
  - Invalid-category rate (model returned something outside the chart)
  - Per-category accuracy

Usage:
    python Testing_Script/Testing_Script.py
"""
from __future__ import annotations

import os
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from categorizer.few_shot.few_shot import COMPANY_CONTEXT, SAMPLE_TRANSACTIONS  # noqa: E402
from categorizer.schemas import CompanyContext, Transaction  # noqa: E402
from categorizer.services.categorizer import CategorizerService  # noqa: E402


def main() -> int:
    ctx = CompanyContext(**COMPANY_CONTEXT)
    service = CategorizerService()

    correct = 0
    confidences: list[float] = []
    invalid_count = 0
    per_category_total: dict[str, int] = defaultdict(int)
    per_category_correct: dict[str, int] = defaultdict(int)

    for i, case in enumerate(SAMPLE_TRANSACTIONS, start=1):
        txn = Transaction(**case["transaction"])
        expected = case["expected_category"]

        if i > 1:
            time.sleep(2)

        result = service.categorize(txn, ctx)
        match = result.category == expected
        correct += int(match)
        confidences.append(result.confidence)
        per_category_total[expected] += 1
        if match:
            per_category_correct[expected] += 1
        if not result.is_valid_category:
            invalid_count += 1

        status_str = "OK" if match else "MISS"
        print(f"# Input")
        print(f"   - Category: {expected}")
        print(f"   - Description: {txn.description}")
        print(f"   - Payee: {txn.payee or 'N/A'}")
        print()
        print(f"# Categorization Agent")
        print(f"   - Category: {result.category}  [{status_str}]")
        print(f"   - Confidence: {result.confidence:.2f}")
        print(f"   - Reason: {result.reasoning}")
        print("~" * 60)

    n = len(SAMPLE_TRANSACTIONS)
    avg_conf = sum(confidences) / n if n else 0.0

    print("=" * 100)
    print(f"\nOverall accuracy:      {correct}/{n} ({correct / n:.0%})")
    print(f"Average confidence:  {avg_conf:.2f}")
    print(f"Invalid categories:  {invalid_count}/{n}")

    print("\nPer-category accuracy:")
    for cat in sorted(per_category_total):
        total = per_category_total[cat]
        got = per_category_correct.get(cat, 0)
        print(f"  {cat:<30} {got}/{total}")

    return 0 if correct == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
