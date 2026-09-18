import requests
import yaml
import json
import os
import time
from datetime import datetime, timezone


GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

HEADERS = {
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": "cncf-good-first-issues-fetcher"
}

if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"


def get_live_cncf_repos():
    """Stage 1: Fetch official CNCF projects directly from the GitHub source YAML."""

    print(
        "Fetching CNCF landscape source directly from GitHub "
        "(Bypassing Cloudflare)..."
    )

    try:
        response = requests.get(
            "https://raw.githubusercontent.com/cncf/landscape/master/landscape.yml",
            headers={
                "User-Agent": "cncf-good-first-issues-fetcher"
            },
            timeout=30
        )

        if response.status_code != 200:
            print(f"Failed to fetch YAML: {response.status_code}")
            return {}

        landscape_data = yaml.safe_load(response.text)

    except requests.exceptions.RequestException as e:
        print(f"Failed to download CNCF landscape YAML: {e}")
        return {}

    except Exception as e:
        print(f"Failed to parse landscape YAML: {e}")
        return {}

    repos = {}

    for category in landscape_data.get("landscape") or []:

        for subcategory in category.get("subcategories") or []:

            for item in subcategory.get("items") or []:

                project_tier = item.get("project")

                if project_tier in ["graduated", "incubating", "sandbox"]:

                    repo_url = item.get("repo_url", "")

                    if repo_url and repo_url.startswith(
                        "https://github.com/"
                    ):

                        repo_path = (
                            repo_url
                            .replace(
                                "https://github.com/",
                                ""
                            )
                            .strip("/")
                            .replace(".git", "")
                        )

                        repos[repo_path] = {
                            "tier": project_tier.capitalize(),
                            "lang": "Unknown"
                        }

    print(
        f"Discovered {len(repos)} official CNCF GitHub repositories."
    )

    return repos


def github_get(url, params=None, max_retries=3):
    """
    Wrapper around requests.get that retries on rate limits
    instead of silently giving up.
    """

    for attempt in range(max_retries):

        try:

            response = requests.get(
                url,
                headers=HEADERS,
                params=params,
                timeout=30
            )

        except requests.exceptions.RequestException as e:

            print(
                f"Connection error: {e}"
            )

            wait_seconds = 5 * (attempt + 1)

            print(
                f"Retrying in {wait_seconds} seconds..."
            )

            time.sleep(wait_seconds)

            continue

        if response.status_code == 200:
            return response

        if response.status_code in (403, 429):

            remaining = response.headers.get(
                "X-RateLimit-Remaining"
            )

            reset_ts = response.headers.get(
                "X-RateLimit-Reset"
            )

            if remaining == "0" and reset_ts:

                wait_seconds = max(
                    int(reset_ts) - int(time.time()),
                    1
                )

                print(
                    f"Rate limited. Sleeping {wait_seconds}s "
                    f"until reset "
                    f"(attempt {attempt + 1}/{max_retries})..."
                )

                time.sleep(
                    min(wait_seconds, 60)
                )

            else:

                wait_seconds = 5 * (attempt + 1)

                print(
                    f"Got {response.status_code}. "
                    f"Backing off {wait_seconds}s "
                    f"(attempt {attempt + 1}/{max_retries})..."
                )

                time.sleep(wait_seconds)

            continue

        print(
            f"Request failed: {response.status_code} "
            f"for {url}"
        )

        return response

    print(
        f"Giving up on {url} after "
        f"{max_retries} retries."
    )

    return None


def fetch_issues(repos):
    """Stage 2: Hunt for beginner issues and repo metadata."""

    labels = [
        "good first issue",
        "good-first-issue",
        "help wanted"
    ]

    all_issues = []

    for repo, meta in repos.items():

        print(
            f"Checking {repo} ({meta['tier']})..."
        )

        repo_res = github_get(
            f"https://api.github.com/repos/{repo}"
        )

        if repo_res and repo_res.status_code == 200:

            meta["lang"] = (
                repo_res.json().get("language")
                or "Unknown"
            )

        for label in labels:

            url = (
                f"https://api.github.com/repos/"
                f"{repo}/issues"
            )

            params = {
                "state": "open",
                "labels": label,
                "per_page": 30
            }

            response = github_get(
                url,
                params=params
            )

            if response and response.status_code == 200:

                issues = response.json()

                for issue in issues:

                    # Ignore pull requests
                    if "pull_request" not in issue:

                        issue_data = {
                            "repo": repo,
                            "tier": meta["tier"],
                            "lang": meta["lang"],
                            "title": issue["title"],
                            "url": issue["html_url"],
                            "labels": [
                                l["name"]
                                for l in issue["labels"]
                            ],
                            "created_at": issue["created_at"]
                        }

                        if issue_data not in all_issues:

                            all_issues.append(
                                issue_data
                            )

        time.sleep(0.5)

    # Save issue data
    with open("data.json", "w") as f:

        json.dump(
            all_issues,
            f,
            indent=4
        )

    # Save the time when the data was updated
    last_updated = datetime.now(
        timezone.utc
    ).isoformat()

    with open("metadata.json", "w") as f:

        json.dump(
            {
                "last_updated": last_updated
            },
            f,
            indent=4
        )

    print("\n--- SUCCESS ---")

    print(
        f"Saved {len(all_issues)} "
        f"total beginner issues to data.json"
    )

    print(
        f"Last updated timestamp saved to "
        f"metadata.json: {last_updated}"
    )


if __name__ == "__main__":

    cncf_repos = get_live_cncf_repos()

    if cncf_repos:

        fetch_issues(cncf_repos)

    else:

        print(
            "No repositories found to scan. "
            "Halting process."
        )