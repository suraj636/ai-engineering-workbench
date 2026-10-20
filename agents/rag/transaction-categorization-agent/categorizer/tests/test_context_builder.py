from django.test import SimpleTestCase

from categorizer.schemas import HistoricalTxn, Transaction
from categorizer.services.context_builder import build_prompt
from categorizer.services.retrieval import RetrievalResult


def _retrieval(**overrides):
    base = dict(
        candidate_categories=["Software Subscriptions", "Travel & Meals"],
        examples_by_category={},
        used_shortlist=True,
    )
    base.update(overrides)
    return RetrievalResult(**base)


class BuildPromptTests(SimpleTestCase):
    def test_includes_candidate_categories_block(self):
        prompt = build_prompt(
            Transaction(description="x"),
            _retrieval(),
            industry="SaaS",
        )
        self.assertIn("<candidate_categories>", prompt)
        self.assertIn("Software Subscriptions", prompt)

    def test_includes_industry(self):
        prompt = build_prompt(
            Transaction(description="x"),
            _retrieval(),
            industry="Construction",
        )
        self.assertIn("Construction", prompt)

    def test_includes_other_when_shortlisted(self):
        prompt = build_prompt(
            Transaction(description="x"),
            _retrieval(used_shortlist=True),
            industry="SaaS",
        )
        self.assertIn("Other", prompt)

    def test_no_other_when_full_chart(self):
        prompt = build_prompt(
            Transaction(description="x"),
            _retrieval(used_shortlist=False),
            industry="SaaS",
        )
        lines = [l.strip() for l in prompt.split("\n") if l.strip()]
        categories_section = False
        other_found = False
        for line in lines:
            if "<candidate_categories>" in line:
                categories_section = True
            elif "</candidate_categories>" in line:
                categories_section = False
            elif categories_section and line.strip().endswith("Other"):
                other_found = True
        self.assertFalse(other_found)

    def test_no_history_renders_placeholder(self):
        prompt = build_prompt(
            Transaction(description="x"),
            _retrieval(examples_by_category={}),
            industry="SaaS",
        )
        self.assertIn("(no historical examples available)", prompt)

    def test_formats_examples_by_category(self):
        examples = {
            "Software Subscriptions": [
                HistoricalTxn(description="AWS bill", payee="Amazon", category="Software Subscriptions"),
            ],
        }
        prompt = build_prompt(
            Transaction(description="x"),
            _retrieval(examples_by_category=examples),
            industry="SaaS",
        )
        self.assertIn("[Software Subscriptions]", prompt)
        self.assertIn("AWS bill", prompt)
