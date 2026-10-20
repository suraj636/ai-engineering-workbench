import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from categorizer.llm_provider.base import LLMResponse


SAMPLE_REQUEST = {
    "transaction": {
        "description": "Figma Professional monthly",
        "payee": "Figma Inc",
    },
    "company_context": {
        "company_id": "acme-001",
        "industry": "SaaS",
        "chart_of_accounts": ["Software Subscriptions", "Travel & Meals", "Bank Fees"],
        "historical_transactions": [
            {"description": "Notion team plan", "payee": "Notion", "category": "Software Subscriptions"},
        ],
    },
}


class CategorizeEndpointTests(SimpleTestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse("categorize")

    def _patched_llm(self, payload):
        return patch(
            "categorizer.services.categorizer.get_llm_client",
            return_value=_FakeClient(payload),
        )

    def test_happy_path_returns_200_and_expected_shape(self):
        payload = {"category": "Software Subscriptions", "confidence": 0.9, "reasoning": "saas tool"}
        with self._patched_llm(payload):
            resp = self.client.post(self.url, SAMPLE_REQUEST, format="json")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["category"], "Software Subscriptions")
        self.assertTrue(body["is_valid_category"])
        self.assertIn("confidence", body)
        self.assertIn("model", body)

    def test_invalid_category_flagged(self):
        payload = {"category": "Made Up", "confidence": 0.9, "reasoning": "guess"}
        with self._patched_llm(payload):
            resp = self.client.post(self.url, SAMPLE_REQUEST, format="json")

        body = resp.json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(body["category"], "Uncategorized")
        self.assertFalse(body["is_valid_category"])
        self.assertNotIn("suggested_category_raw", body)

    def test_missing_fields_returns_400(self):
        bad = {"transaction": {}, "company_context": {}}
        resp = self.client.post(self.url, bad, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "invalid_request")


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload

    @property
    def model_name(self):
        return "fake-llm"

    def complete_json(self, *, system_prompt, user_prompt, response_schema):
        return LLMResponse(text=json.dumps(self._payload), model="fake-llm")
