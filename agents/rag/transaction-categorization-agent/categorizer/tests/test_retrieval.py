from django.test import SimpleTestCase

from categorizer.schemas import HistoricalTxn
from categorizer.services.retrieval import select_few_shot


def _history():
    return [
        HistoricalTxn(description="AWS Cloud Services", payee="Amazon Web Services", category="Software Subscriptions"),
        HistoricalTxn(description="GitHub Team plan", payee="GitHub", category="Software Subscriptions"),
        HistoricalTxn(description="Uber ride to client", payee="Uber", category="Travel & Meals"),
        HistoricalTxn(description="Team lunch", payee="Chipotle", category="Travel & Meals"),
        HistoricalTxn(description="LinkedIn Ads campaign", payee="LinkedIn", category="Marketing & Advertising"),
        HistoricalTxn(description="Google Ads", payee="Google", category="Marketing & Advertising"),
        HistoricalTxn(description="Legal retainer", payee="Smith LLP", category="Professional Services"),
        HistoricalTxn(description="Wire transfer fee", payee="Chase Bank", category="Bank Fees"),
    ]


CHART = [
    "Software Subscriptions", "Travel & Meals", "Marketing & Advertising",
    "Professional Services", "Bank Fees",
]


class SelectFewShotTests(SimpleTestCase):
    def test_returns_all_categories_for_small_chart(self):
        result = select_few_shot(
            "Figma monthly", "Figma Inc", _history(), CHART,
        )
        self.assertFalse(result.used_shortlist)
        self.assertEqual(len(result.candidate_categories), len(CHART))

    def test_shortlists_for_large_chart(self):
        large_chart = CHART + [f"Category {i}" for i in range(10)]
        result = select_few_shot(
            "Figma monthly", "Figma Inc", _history(), large_chart,
        )
        self.assertTrue(result.used_shortlist)
        self.assertLessEqual(len(result.candidate_categories), 5)

    def test_exact_payee_pinned(self):
        large_chart = CHART + [f"Category {i}" for i in range(10)]
        result = select_few_shot(
            "Wire fee", "Chase Bank", _history(), large_chart,
        )
        self.assertIn("Bank Fees", result.candidate_categories)

    def test_empty_history_returns_full_chart(self):
        result = select_few_shot("Figma", None, [], CHART)
        self.assertFalse(result.used_shortlist)
        self.assertEqual(result.candidate_categories, CHART)
        self.assertEqual(result.examples_by_category, {})

    def test_examples_per_category_capped(self):
        result = select_few_shot(
            "AWS bill", "Amazon Web Services", _history(), CHART,
        )
        for examples in result.examples_by_category.values():
            self.assertLessEqual(len(examples), 2)
