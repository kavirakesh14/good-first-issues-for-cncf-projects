import json
import os
import tempfile
import unittest
from unittest.mock import patch

from fetcher import fetch_issues, is_ai_native, normalize


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

    def test_extra_ai_true_matches(self):
        self.assertTrue(
            is_ai_native(
                {"name": "KServe", "extra": {"ai": True}},
                {"name": "Cloud Native Computing"},
                {"name": "Serving"},
            )
        )

    def test_extra_ai_false_does_not_override_other_metadata(self):
        self.assertFalse(
            is_ai_native(
                {"name": "Example", "description": "General developer tools",
                 "extra": {"ai": False}},
                {"name": "Other"},
                {"name": "Other"},
            )
        )

    def test_second_path_ai_native_infra_matches(self):
        self.assertTrue(
            is_ai_native(
                {"name": "Example", "second_path": ["AI Native Infra"]},
                {"name": "Other"},
                {"name": "Other"},
            )
        )

    def test_second_path_hierarchical_ai_native_infra_matches(self):
        self.assertTrue(
            is_ai_native(
                {"name": "Example", "second_path": ["AI Native Infra / Gateway"]},
                {"name": "Other"},
                {"name": "Other"},
            )
        )

    def test_second_path_dict_name_matches(self):
        self.assertTrue(
            is_ai_native(
                {"name": "Example", "second_path": [{"name": "AI Native Infra"}]},
                {"name": "Other"},
                {"name": "Other"},
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

    def test_ai_related_description_is_fallback(self):
        self.assertTrue(
            is_ai_native(
                {"name": "Example", "description": "A machine learning runtime"},
                {"name": "Other"},
                {"name": "Other"},
            )
        )

    def test_generic_training_category_does_not_match(self):
        self.assertFalse(
            is_ai_native(
                {"name": "Example", "description": "General developer tools"},
                {"name": "Training"},
                {"name": "Other"},
            )
        )

    def test_generic_inference_category_does_not_match(self):
        self.assertFalse(
            is_ai_native(
                {"name": "Example", "description": "A data processing engine"},
                {"name": "Other"},
                {"name": "Inference"},
            )
        )

    def test_keyword_must_be_a_complete_term(self):
        self.assertFalse(
            is_ai_native(
                {"name": "LLMonitor", "description": "Monitor application logs"},
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


class IssueDataPreservationTests(unittest.TestCase):
    def test_classification_metadata_and_issue_fields_are_written(self):
        repo = "example/project"
        issue = {
            "title": "Keep issue metadata",
            "html_url": "https://github.com/example/project/issues/1",
            "labels": [{"name": "good first issue"}, {"name": "help wanted"}],
            "created_at": "2026-01-02T03:04:05Z",
        }

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        def fake_github_get(url, params=None):
            if url.endswith("/repos/example/project"):
                return Response({"language": "Python"})
            return Response([issue])

        repos = {
            repo: {
                "tier": "Sandbox",
                "lang": "Unknown",
                "category": "AI Native",
                "subcategory": "Infrastructure",
                "description": "Project description",
                "ai_related": True,
            }
        }

        with tempfile.TemporaryDirectory() as directory:
            output_path = os.path.join(directory, "data.json")
            with patch("fetcher.github_get", side_effect=fake_github_get), patch(
                "fetcher.time.sleep"
            ):
                result = fetch_issues(repos, output_path)

            with open(output_path, encoding="utf-8") as data_file:
                saved = json.load(data_file)

        self.assertEqual(len(result), 1)
        self.assertEqual(saved, result)
        self.assertEqual(saved[0]["ai_related"], True)
        self.assertEqual(saved[0]["repo"], repo)
        self.assertEqual(saved[0]["title"], issue["title"])
        self.assertEqual(saved[0]["url"], issue["html_url"])
        self.assertEqual(saved[0]["labels"], ["good first issue", "help wanted"])
        self.assertEqual(saved[0]["created_at"], issue["created_at"])
        self.assertEqual(saved[0]["lang"], "Python")


if __name__ == "__main__":
    unittest.main()
