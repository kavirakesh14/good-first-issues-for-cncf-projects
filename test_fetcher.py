import json

import fetcher
import os
import tempfile
import unittest
from unittest.mock import patch

from fetcher import enrich_existing_issues, fetch_issues, is_ai_native, normalize


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

    def test_second_path_cross_listing_does_not_classify_project(self):
        self.assertFalse(
            is_ai_native(
                {"name": "Kubernetes", "second_path": ["AI Native Infra"]},
                {"name": "Infrastructure"},
                {"name": "Container Runtime"},
            )
        )

    def test_second_path_hierarchical_cross_listing_does_not_classify(self):
        self.assertFalse(
            is_ai_native(
                {"name": "Prometheus", "second_path": ["AI Native Infra / Gateway"]},
                {"name": "Observability"},
                {"name": "Monitoring"},
            )
        )

    def test_second_path_dict_cross_listing_does_not_classify(self):
        self.assertFalse(
            is_ai_native(
                {"name": "Cilium", "second_path": [{"name": "AI Native Infra"}]},
                {"name": "Networking"},
                {"name": "Service Mesh"},
            )
        )
    def test_known_non_ai_projects_are_not_classified_from_cross_listing(self):
        examples = (
            ("kubernetes/kubernetes", "Kubernetes", "Infrastructure", "Container Runtime"),
            ("prometheus/prometheus", "Prometheus", "Observability", "Monitoring"),
            ("cilium/cilium", "Cilium", "Networking", "Service Mesh"),
            ("keycloak/keycloak", "Keycloak", "Identity", "Authentication"),
            ("cri-o/cri-o", "CRI-O", "Runtime", "Container Runtime"),
            ("kyverno/kyverno", "Kyverno", "Security", "Policy"),
        )
        for repo, name, category, subcategory in examples:
            with self.subTest(repo=repo):
                self.assertFalse(
                    is_ai_native(
                        {"name": name, "second_path": ["AI Native Infra"]},
                        {"name": category},
                        {"name": subcategory},
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


class LandscapeMetadataParsingTests(unittest.TestCase):
    def test_live_parser_reads_explicit_ai_and_ignores_second_path(self):
        landscape_yaml = """
landscape:
  - name: Cloud Native
    subcategories:
      - name: Infrastructure
        items:
          - name: Explicit AI project
            project: sandbox
            repo_url: https://github.com/example/explicit-ai
            extra:
              ai: true
          - name: AI Native path project
            project: incubating
            repo_url: https://github.com/example/path-ai.git
            second_path: AI Native Infra / Gateway
"""
        class Response:
            text = landscape_yaml

            def raise_for_status(self):
                return None

        with patch("fetcher.SESSION.get", return_value=Response()):
            repos = fetcher.get_live_cncf_repos()

        self.assertTrue(repos["example/explicit-ai"]["ai_related"])
        self.assertFalse(repos["example/path-ai"]["ai_related"])
        self.assertEqual(repos["example/explicit-ai"]["tier"], "Sandbox")
        self.assertEqual(repos["example/path-ai"]["tier"], "Incubating")

    def test_enrichment_refuses_to_rewrite_when_no_repository_matches(self):
        original = [{
            "repo": "example/not-in-landscape",
            "title": "Keep this issue",
            "url": "https://github.com/example/not-in-landscape/issues/1",
        }]
        with tempfile.TemporaryDirectory() as directory:
            output_path = os.path.join(directory, "data.json")
            with open(output_path, "w", encoding="utf-8") as data_file:
                json.dump(original, data_file)

            with self.assertRaisesRegex(ValueError, "No existing issue repositories matched"):
                enrich_existing_issues({}, output_path)

            with open(output_path, encoding="utf-8") as data_file:
                saved = json.load(data_file)

        self.assertEqual(saved, original)


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

        self.assertEqual(len(result["issues"]), 1)
        self.assertEqual(saved, result)
        self.assertEqual(saved["issues"][0]["ai_related"], True)
        self.assertEqual(saved["issues"][0]["repo"], repo)
        self.assertEqual(saved["issues"][0]["title"], issue["title"])
        self.assertEqual(saved["issues"][0]["url"], issue["html_url"])
        self.assertEqual(saved["issues"][0]["labels"], ["good first issue", "help wanted"])
        self.assertEqual(saved["issues"][0]["created_at"], issue["created_at"])
        self.assertEqual(saved["issues"][0]["lang"], "Python")
        self.assertIn("last_updated", saved)



class ExistingDashboardEnrichmentTests(unittest.TestCase):
    def test_enrichment_preserves_all_existing_issue_records_and_fields(self):
        original = [
            {
                "repo": "example/ai-project",
                "tier": "Sandbox",
                "lang": "Python",
                "title": "Existing issue title",
                "url": "https://github.com/example/ai-project/issues/7",
                "labels": ["help wanted"],
                "created_at": "2026-01-01T00:00:00Z",
                "custom_existing_field": "must survive",
            },
            {
                "repo": "example/unmatched",
                "tier": "Incubating",
                "lang": "Go",
                "title": "Unmatched issue",
                "url": "https://github.com/example/unmatched/issues/8",
                "labels": ["good first issue"],
                "created_at": "2026-01-02T00:00:00Z",
            },
        ]
        repos = {
            "example/ai-project": {
                "category": "AI Native",
                "subcategory": "Infrastructure",
                "description": "AI platform",
                "ai_related": True,
            }
        }

        with tempfile.TemporaryDirectory() as directory:
            output_path = os.path.join(directory, "data.json")
            with open(output_path, "w", encoding="utf-8") as data_file:
                json.dump(original, data_file)

            enriched = enrich_existing_issues(repos, output_path)

            with open(output_path, encoding="utf-8") as data_file:
                saved = json.load(data_file)

        self.assertEqual(len(saved["issues"]), len(original))
        self.assertEqual(saved, enriched)
        self.assertEqual(saved["issues"][0]["url"], original[0]["url"])
        self.assertEqual(saved["issues"][0]["labels"], original[0]["labels"])
        self.assertEqual(saved["issues"][0]["custom_existing_field"], "must survive")
        self.assertTrue(saved["issues"][0]["ai_related"])
        self.assertEqual(saved["issues"][0]["category"], "AI Native")
        self.assertEqual(saved["issues"][1]["title"], original[1]["title"])
        self.assertFalse(saved["issues"][1]["ai_related"])
        self.assertIn("last_updated", saved)


class RepositoryIdentityTests(unittest.TestCase):
    def test_duplicate_entries_keep_positive_ai_classification_and_repo_casing(self):
        landscape_yaml = """
landscape:
  - name: Cloud Native
    subcategories:
      - name: Infrastructure
        items:
          - name: HolmesGPT
            project: sandbox
            repo_url: https://github.com/HolmesGPT/holmesgpt
            description: Operations assistant
          - name: HolmesGPT AI
            project: sandbox
            repo_url: https://github.com/holmesgpt/holmesgpt.git
            extra:
              ai: true
"""
        class Response:
            text = landscape_yaml
            def raise_for_status(self):
                return None
        with patch("fetcher.SESSION.get", return_value=Response()):
            repos = fetcher.get_live_cncf_repos()
        self.assertEqual(len(repos), 1)
        meta = next(iter(repos.values()))
        self.assertEqual(meta["repo"], "HolmesGPT/holmesgpt")
        self.assertTrue(meta["ai_related"])

    def test_enrichment_reads_object_format_and_preserves_repo_casing(self):
        original = {
            "last_updated": "2026-11-01T00:00:00+00:00",
            "issues": [{
                "repo": "HolmesGPT/holmesgpt",
                "title": "Existing issue",
                "url": "https://github.com/HolmesGPT/holmesgpt/issues/1",
                "labels": [],
                "created_at": "2026-11-01T00:00:00Z",
            }],
        }
        repos = {
            "holmesgpt/holmesgpt": {
                "repo": "holmesgpt/holmesgpt",
                "category": "AI Native",
                "subcategory": "Agents",
                "description": "AI assistant",
                "ai_related": True,
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            output_path = os.path.join(directory, "data.json")
            with open(output_path, "w", encoding="utf-8") as data_file:
                json.dump(original, data_file)
            enriched = enrich_existing_issues(repos, output_path)
        self.assertEqual(enriched["issues"][0]["repo"], "HolmesGPT/holmesgpt")
        self.assertTrue(enriched["issues"][0]["ai_related"])
        self.assertIsInstance(enriched["last_updated"], str)


if __name__ == "__main__":
    unittest.main()
