"""Fetch open beginner issues for CNCF projects and annotate AI Native metadata.

AI Native classification prioritizes explicit landscape categories and tags.
Name/description matching is a conservative fallback, not an official CNCF
certification.
"""
import argparse
import json
import os
import re
import time
from datetime import datetime, timezone

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

# Explicit AI-focused categories only. Generic "training" and "inference"
# are intentionally excluded because they occur in many non-AI projects.
AI_NATIVE_CATEGORY_NAMES = {
    "ai native",
    "ai native landscape",
    "ai native infra",
    "ai native infrastructure",
    "ai agents",
    "ai agent",
    "artificial intelligence",
    "machine learning",
    "generative ai",
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

    # CNCF Landscape's explicit AI marker is authoritative.
    extra = item.get("extra")
    if isinstance(extra, dict) and extra.get("ai") is True:
        return True

    if category_name in AI_NATIVE_CATEGORY_NAMES:
        return True
    if subcategory_name in AI_NATIVE_CATEGORY_NAMES:
        return True
    if any("ai-native" in tag or "cnai" in tag for tag in tag_names):
        return True

    # A named workload under an explicitly AI Native parent is meaningful.
    if "ai native" in category_name and subcategory_name:
        return True

    # Conservative fallback: match complete terms, not arbitrary substrings.
    searchable_text = " ".join(
        normalize(value)
        for value in (
            item.get("name"),
            item.get("description"),
            category.get("name"),
            subcategory.get("name"),
        )
    )
    return any(
        re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", searchable_text)
        for term in AI_NATIVE_TERMS
    )


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

                repo = match.group(1)
                repo_key = normalize(repo)
                metadata = {
                    "repo": repo,
                    "tier": tier.capitalize(),
                    "lang": "Unknown",
                    "category": category.get("name", ""),
                    "subcategory": subcategory.get("name", ""),
                    "description": item.get("description", ""),
                    "ai_related": is_ai_native(item, category, subcategory),
                }

                existing = repos.get(repo_key)
                if existing is None:
                    repos[repo_key] = metadata
                else:
                    # A later landscape entry must never erase a positive AI match.
                    existing["ai_related"] = (
                        bool(existing.get("ai_related")) or metadata["ai_related"]
                    )
                    # Preserve the first repository spelling and fill missing metadata.
                    for field in ("category", "subcategory", "description"):
                        if not existing.get(field) and metadata[field]:
                            existing[field] = metadata[field]

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


def _response_json(response, expected_type, context):
    """Decode an API response and reject unexpected payloads."""
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Invalid JSON returned while {context}.") from exc

    if not isinstance(payload, expected_type):
        raise RuntimeError(
            f"Unexpected API response while {context}: "
            f"expected {expected_type.__name__}, got {type(payload).__name__}."
        )
    return payload


def _write_json_atomically(output, output_path):
    """Write JSON without truncating the previous file if writing fails."""
    temp_path = f"{output_path}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as output_file:
            json.dump(output, output_file, indent=2, ensure_ascii=False)
            output_file.write("\n")
        os.replace(temp_path, output_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def fetch_issues(repos, output_path="data.json"):
    """Fetch all open beginner issues; never replace data after a partial fetch."""
    issue_labels = ("good first issue", "good-first-issue", "help wanted")
    per_page = 100
    all_issues = []
    seen_issue_urls = set()

    for repo_key, meta in repos.items():
        repo = meta.get("repo", repo_key)
        print(f"Checking {repo} ({meta['tier']})...")
        repo_response = github_get(f"{GITHUB_API}/repos/{repo}")
        if repo_response is None:
            raise RuntimeError(
                f"Could not fetch repository metadata for {repo}; "
                f"{output_path} was left unchanged."
            )

        repo_data = _response_json(
            repo_response, dict, f"reading repository metadata for {repo}"
        )
        meta["lang"] = repo_data.get("language") or "Unknown"

        for label in issue_labels:
            page = 1
            while True:
                response = github_get(
                    f"{GITHUB_API}/repos/{repo}/issues",
                    params={
                        "state": "open",
                        "labels": label,
                        "per_page": per_page,
                        "page": page,
                    },
                )
                if response is None:
                    raise RuntimeError(
                        f"Could not fetch page {page} of '{label}' issues for "
                        f"{repo}; {output_path} was left unchanged."
                    )

                issues_page = _response_json(
                    response,
                    list,
                    f"reading page {page} of '{label}' issues for {repo}",
                )
                for issue in issues_page:
                    if not isinstance(issue, dict):
                        raise RuntimeError(
                            f"Invalid issue record in {repo} response; "
                            f"{output_path} was left unchanged."
                        )
                    if "pull_request" in issue:
                        continue

                    issue_url = issue.get("html_url")
                    if not issue_url:
                        raise RuntimeError(
                            f"Issue record without html_url in {repo}; "
                            f"{output_path} was left unchanged."
                        )
                    if issue_url in seen_issue_urls:
                        continue

                    issue_labels_data = issue.get("labels", [])
                    if not isinstance(issue_labels_data, list):
                        raise RuntimeError(
                            f"Invalid labels for issue {issue_url}; "
                            f"{output_path} was left unchanged."
                        )

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
                            for label_data in issue_labels_data
                            if isinstance(label_data, dict)
                        ],
                        "created_at": issue.get("created_at", ""),
                    })
                    seen_issue_urls.add(issue_url)

                if len(issues_page) < per_page:
                    break
                page += 1
        time.sleep(0.5)

    output = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "issues": all_issues,
    }
    _write_json_atomically(output, output_path)

    print(f"Saved {len(all_issues)} beginner issues to {output_path}.")
    return output


def enrich_existing_issues(repos, output_path="data.json"):
    """Add current landscape metadata without replacing or dropping dashboard issues."""
    with open(output_path, encoding="utf-8") as data_file:
        data = json.load(data_file)

    if isinstance(data, list):
        issues = data
    elif isinstance(data, dict) and isinstance(data.get("issues"), list):
        issues = data["issues"]
    else:
        raise ValueError(
            'data.json must contain a list or an object with an "issues" list.'
        )

    repos_by_key = {
        normalize(meta.get("repo", repo_key)): meta
        for repo_key, meta in repos.items()
    }
    matched_repos = set()
    matched_issues = 0
    for issue in issues:
        repo = normalize(issue.get("repo", ""))
        meta = repos_by_key.get(repo)
        if meta is None:
            # Preserve unknown records and their existing metadata.
            issue.setdefault("ai_related", False)
            continue

        matched_repos.add(repo)
        matched_issues += 1
        issue["category"] = meta.get("category", "")
        issue["subcategory"] = meta.get("subcategory", "")
        issue["description"] = meta.get("description", "")
        issue["ai_related"] = bool(meta.get("ai_related", False))

    if not matched_repos:
        raise ValueError(
            "No existing issue repositories matched CNCF landscape metadata; "
            "data.json was not changed."
        )

    # Write to a temporary file and replace atomically to avoid truncating
    # the dashboard data if the process is interrupted during serialization.
    temp_path = f"{output_path}.tmp"
    output = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "issues": issues,
    }
    try:
        with open(temp_path, "w", encoding="utf-8") as data_file:
            json.dump(output, data_file, indent=2, ensure_ascii=False)
            data_file.write("\n")
        os.replace(temp_path, output_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    ai_issue_count = sum(issue.get("ai_related") is True for issue in issues)
    print(
        f"Preserved {len(issues)} existing issues; enriched {matched_issues} "
        f"issues across {len(matched_repos)} matched repositories."
    )
    print(f"AI Native issues in existing dashboard data: {ai_issue_count}")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync or enrich CNCF dashboard issue data.")
    parser.add_argument(
        "--enrich-existing",
        action="store_true",
        help="Annotate existing data.json records without fetching, replacing, or dropping issues.",
    )
    args = parser.parse_args()

    cncf_repos = get_live_cncf_repos()
    if not cncf_repos:
        raise SystemExit(
            "No repositories were fetched. data.json was left unchanged."
        )

    if args.enrich_existing:
        enrich_existing_issues(cncf_repos)
    else:
        fetch_issues(cncf_repos)
