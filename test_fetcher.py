import unittest

from fetcher import is_ai_native, normalize


class AiNativeClassificationTests(unittest.TestCase):
    def test_normalize_handles_case_and_whitespace(self):
        self.assertEqual(normalize("  AI   Agents "), "ai agents")

    def test_explicit_ai_native_category_matches(self):
        self.assertTrue(
            is_ai_native(
                {"name": "Example", "description": ""},
                {"name": "AI Agents"},
                {"name": "Agent Framework"},
            )
        )

    def test_ai_native_tag_matches(self):
        self.assertTrue(
            is_ai_native(
                {"name": "Example", "tags": ["cnai"]},
                {"name": "Other"},
                {"name": "Other"},
            )
        )

    def test_description_keyword_is_fallback(self):
        self.assertTrue(
            is_ai_native(
                {"name": "Example", "description": "A machine learning runtime"},
                {"name": "Other"},
                {"name": "Other"},
            )
        )

    def test_unrelated_project_does_not_match(self):
        self.assertFalse(
            is_ai_native(
                {"name": "Container runtime", "description": "Manage containers"},
                {"name": "Runtime"},
                {"name": "Container runtime"},
            )
        )


if __name__ == "__main__":
    unittest.main()
