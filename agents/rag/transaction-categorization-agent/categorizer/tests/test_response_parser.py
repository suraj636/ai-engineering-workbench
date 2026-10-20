from django.test import SimpleTestCase

from categorizer.services.response_parser import ParseError, category_is_valid, parse_suggestion


class ParseSuggestionTests(SimpleTestCase):
    def test_parses_clean_json(self):
        raw = '{"category": "Travel & Meals", "confidence": 0.92, "reasoning": "lunch"}'
        s = parse_suggestion(raw)
        self.assertEqual(s.category, "Travel & Meals")
        self.assertAlmostEqual(s.confidence, 0.92)

    def test_salvages_json_wrapped_in_prose(self):
        raw = 'Sure, here is the result:\n{"category": "Utilities", "confidence": 0.7, "reasoning": "electric"}\nLet me know if you need more.'
        s = parse_suggestion(raw)
        self.assertEqual(s.category, "Utilities")

    def test_raises_on_garbage(self):
        with self.assertRaises(ParseError):
            parse_suggestion("not json at all")

    def test_raises_on_missing_required_fields(self):
        with self.assertRaises(ParseError):
            parse_suggestion('{"reasoning": "no category here"}')


class CategoryValidationTests(SimpleTestCase):
    def test_known_category(self):
        self.assertTrue(category_is_valid("Utilities", ["Utilities", "Bank Fees"]))

    def test_unknown_category(self):
        self.assertFalse(category_is_valid("Made Up", ["Utilities", "Bank Fees"]))
