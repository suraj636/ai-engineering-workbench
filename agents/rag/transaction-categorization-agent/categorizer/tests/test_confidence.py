from django.test import SimpleTestCase

from categorizer.schemas import HistoricalTxn
from categorizer.services.confidence import (
    EXACT_PAYEE_MATCH_BOOST,
    OTHER_CATEGORY_BOOST,
    adjust_confidence,
)


class AdjustConfidenceTests(SimpleTestCase):
    def test_invalid_category_caps_confidence(self):
        result = adjust_confidence(0.95, payee=None, predicted_category="Foo", history=[], is_valid=False)
        self.assertLessEqual(result, 0.3)

    def test_payee_match_bumps_confidence(self):
        history = [HistoricalTxn(description="Old txn", payee="Uber", category="Travel & Meals")]
        bumped = adjust_confidence(
            0.7, payee="uber", predicted_category="Travel & Meals", history=history, is_valid=True
        )
        self.assertGreater(bumped, 0.7)

    def test_payee_match_with_wrong_category_no_bump(self):
        history = [HistoricalTxn(description="Old txn", payee="Uber", category="Travel & Meals")]
        same = adjust_confidence(
            0.7, payee="Uber", predicted_category="Software Subscriptions", history=history, is_valid=True
        )
        self.assertAlmostEqual(same, 0.7)

    def test_other_category_boost(self):
        result = adjust_confidence(
            0.5, payee=None, predicted_category="Other", history=[], is_valid=True,
        )
        self.assertAlmostEqual(result, 0.5 + OTHER_CATEGORY_BOOST)

    def test_other_plus_payee_match_stacks_boosts(self):
        history = [
            HistoricalTxn(
                description="Office rent",
                payee="WeWork",
                category="Other",
            ),
        ]
        result = adjust_confidence(
            0.8,
            payee="WeWork",
            predicted_category="Other",
            history=history,
            is_valid=True,
        )
        self.assertAlmostEqual(
            result,
            0.8 + OTHER_CATEGORY_BOOST + EXACT_PAYEE_MATCH_BOOST,
        )

    def test_clamped_to_one(self):
        history = [HistoricalTxn(description="x", payee="Stripe", category="Bank Fees")]
        result = adjust_confidence(0.99, payee="Stripe", predicted_category="Bank Fees", history=history, is_valid=True)
        self.assertLessEqual(result, 1.0)
