"""Fetch open beginner issues for CNCF projects and annotate AI Native metadata.

The AI Native filter is based on explicit category/subcategory/tag metadata when
available. Name/description keyword matching is a fallback, so the field is a
discovery aid rather than an official CNCF certification.
"""
import json
import os
import re
import time

import requests
import yaml

LANDSCAPE_URL = (
    "https://raw.githubusercontent.com/cncf/landscape/master/landscape.yml"
)
GITHUB_API = "https://api.github.com"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {"Accept": "application/vnd.github+json"}
if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"Bearer {GITHUB_TOKEN}"

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# These are workload names used by the CNCF AI Native Landscape.
AI_NATIVE_CATEGORY_NAMES = {
    "ai native",
    "ai native landscape",
    "ai native infra",
    "ai native infrastructure",
    "ai agents",
    "inference",
    "training",
}
AI_NATIVE_TERMS = (
    "artificial intelligence",
    "machine learning",
    "generative ai",
    "large language model",
    "large language models",
    "llm",
    "llms",
    "ai agent",
    "ai agents",
    "model inference",
    "model training",
)


def normalize(value):
    """Normalize optional metadata for safe, case-insensitive comparisons."""
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def is_ai_native(item, category, subcategory):
    """Return whether landscape metadata indicates an AI Native project."""
    category_name = normalize(category.get("name"))
    subcategory_name = normalize(subcategory.get("name"))
    item_tags = item.get("tags") or []
    tag_names = {
        normalize(tag.get("name") if isinstance(tag, dict) else tag)
        for tag in item_tags
    }

    if category_name in AI_NATIVE_CATEGORY_NAMES:
        return True
    if subcategory_name in AI_NATIVE_CATEGORY_NAMES:
        return True
    if any("ai-native" in tag or "cnai" in tag for tag in tag_names):
        return True

    # The AI Native Landscape's workload subcategories are meaningful only
    # when their parent category identifies the AI Native landscape.
    if (
        "ai native" in category_name
        and subcategory_name
    ):
        return True

    # Fallback for sources that expose project descriptions but not category
    # metadata. Keep this broad enough for discovery, and document its limits.
    searchable_text = " ".join(
        normalize(value)
        for value in (
            item.get("name"),
            item.get("description"),
            category.get("name"),
            subcategory.get("name"),
        )
    )
    return any(term in searchable_text for term in AI_NATIVE_TERMS)


def get_live_cncf_repos():
    """Fetch project repository metadata from the canonical landscape YAML."""
    print("Fetching CNCF landscape source from GitHub...")
    try:
        response = SESSION.get(LANDSCAPE_URL, timeout=30)
        response.raise_for_status()
        landscape_data = yaml.safe_load(response.text) or {}
    except (requests.RequestException, yaml.YAMLError) as exc:
        print(f"Could not fetch or parse landscape YAML: {exc}")
        return {}

    repos = {}
    for category in landscape_data.get("landscape") or []:
        for subcategory in category.get("subcategories") or []:
            for item in subcategory.get("items") or []:
                tier = normalize(item.get("project"))
                if tier not in {"graduated", "incubating", "sandbox"}:
                    continue

                repo_url = item.get("repo_url")
                if not isinstance(repo_url, str):
                    continue
                match = re.fullmatch(
                    r"https://github\.com/([^/]+/[^/]+)/?",
                    repo_url.strip().removesuffix(".git"),
                    flags=re.IGNORECASE,
                )
                if not match:
                    continue

                repo = match.group(1).lower()
                repos[repo] = {
                    "tier": tier.capitalize(),
                    "lang": "Unknown",
                    "category": category.get("name", ""),
                    "subcategory": subcategory.get("name", ""),
                    "description": item.get("description", ""),
                    "ai_related": is_ai_native(item, category, subcategory),
                }

    print(f"Discovered {len(repos)} official CNCF GitHub repositories.")
    print(
        "Potentially AI Native repositories: "
        f"{sum(meta['ai_related'] for meta in repos.values())}"
    )
    return repos


def github_get(url, params=None, max_retries=3):
    """GET a GitHub API endpoint with basic network/rate-limit retries."""
    for attempt in range(max_retries):
        try:
            response = SESSION.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            print(f"Request error for {url}: {exc}")
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            return None

        if response.status_code == 200:
            return response

        if response.status_code in (403, 429):
            remaining = response.headers.get("X-RateLimit-Remaining")
            reset = response.headers.get("X-RateLimit-Reset")
            wait = 2 * (attempt + 1)
            if remaining == "0" and reset:
                wait = min(max(int(reset) - int(time.time()), 1), 60)
            if attempt < max_retries - 1:
                print(f"GitHub rate limit; waiting {wait}s before retry.")
                time.sleep(wait)
                continue

        print(f"GitHub request failed ({response.status_code}): {url}")
        return None
    return None


def fetch_issues(repos, output_path="data.json"):
    """Fetch open beginner issues and write the dashboard's JSON data."""
    issue_labels = ("good first issue", "good-first-issue", "help wanted")
    all_issues = []
    seen_issue_urls = set()

    for repo, meta in repos.items():
        print(f"Checking {repo} ({meta['tier']})...")
        repo_response = github_get(f"{GITHUB_API}/repos/{repo}")
        if repo_response is not None:
            meta["lang"] = repo_response.json().get("language") or "Unknown"

        for label in issue_labels:
            response = github_get(
                f"{GITHUB_API}/repos/{repo}/issues",
                params={"state": "open", "labels": label, "per_page": 30},
            )
            if response is None:
                continue

            for issue in response.json():
                if "pull_request" in issue:
                    continue
                issue_url = issue.get("html_url")
                if not issue_url or issue_url in seen_issue_urls:
                    continue

                all_issues.append({
                    "repo": repo,
                    "tier": meta["tier"],
                    "lang": meta["lang"],
                    "category": meta.get("category", ""),
                    "subcategory": meta.get("subcategory", ""),
                    "description": meta.get("description", ""),
                    "ai_related": bool(meta.get("ai_related", False)),
                    "title": issue.get("title", ""),
                    "url": issue_url,
                    "labels": [
                        label_data.get("name", "")
                        for label_data in issue.get("labels", [])
                    ],
                    "created_at": issue.get("created_at", ""),
                })
                seen_issue_urls.add(issue_url)
        time.sleep(0.5)

    with open(output_path, "w", encoding="utf-8") as output_file:
        json.dump(all_issues, output_file, indent=2, ensure_ascii=False)

    print(f"Saved {len(all_issues)} beginner issues to {output_path}.")
    return all_issues


if __name__ == "__main__":
    cncf_repos = get_live_cncf_repos()
    if cncf_repos:
        fetch_issues(cncf_repos)
    else:
        raise SystemExit(
            "No repositories were fetched. data.json was left unchanged."
        )
