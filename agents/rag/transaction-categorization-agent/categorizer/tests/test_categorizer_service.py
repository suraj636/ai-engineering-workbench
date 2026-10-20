import json

from django.test import SimpleTestCase

from categorizer.llm_provider.base import LLMClient, LLMResponse
from categorizer.schemas import CompanyContext, HistoricalTxn, Transaction
from categorizer.services.categorizer import CategorizerService


class _StubLLM(LLMClient):
    def __init__(self, payload: dict, model: str = "stub-1"):
        self._payload = payload
        self._model = model

    @property
    def model_name(self):
        return self._model

    def complete_json(self, *, system_prompt, user_prompt, response_schema):
        return LLMResponse(text=json.dumps(self._payload), model=self._model)


def _ctx():
    return CompanyContext(
        company_id="c1",
        industry="SaaS",
        chart_of_accounts=["Software Subscriptions", "Travel & Meals", "Bank Fees"],
        historical_transactions=[
            HistoricalTxn(description="AWS", payee="Amazon Web Services", category="Software Subscriptions"),
        ],
    )


class CategorizerServiceTests(SimpleTestCase):
    def test_returns_valid_category_unchanged(self):
        svc = CategorizerService(llm=_StubLLM({
            "category": "Software Subscriptions",
            "confidence": 0.8,
            "reasoning": "matches AWS pattern",
        }))
        result = svc.categorize(
            Transaction(description="AWS bill", payee="Amazon Web Services"),
            _ctx(),
        )
        self.assertEqual(result.category, "Software Subscriptions")
        self.assertTrue(result.is_valid_category)
        self.assertGreater(result.confidence, 0.8)

    def test_unknown_category_flagged_and_demoted(self):
        svc = CategorizerService(llm=_StubLLM({
            "category": "Spaceship Fuel",
            "confidence": 0.95,
            "reasoning": "weird",
        }))
        result = svc.categorize(Transaction(description="???"), _ctx())
        self.assertEqual(result.category, "Uncategorized")
        self.assertFalse(result.is_valid_category)
        self.assertLessEqual(result.confidence, 0.3)

    def test_other_reasoning_trimmed(self):
        long_reason = (
            "The provided description does not match the existing descriptions of the "
            "categories. Its category should be \"Office Rent\". Extra words here."
        )
        svc = CategorizerService(llm=_StubLLM({
            "category": "Other",
            "confidence": 0.3,
            "reasoning": long_reason,
        }))
        ctx = CompanyContext(
            company_id="c1",
            industry="SaaS",
            chart_of_accounts=["Software Subscriptions", "Travel & Meals", "Bank Fees", "Other"],
            historical_transactions=_ctx().historical_transactions,
        )
        result = svc.categorize(Transaction(description="Office rent payment"), ctx)
        self.assertEqual(result.category, "Other")
        self.assertTrue(result.is_valid_category)
        self.assertGreater(result.confidence, 0.3)
        self.assertLessEqual(len((result.reasoning or "").split()), 30)
        self.assertIn("Office Rent", result.reasoning)
