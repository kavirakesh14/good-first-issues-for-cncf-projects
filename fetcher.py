import requests
import yaml
import json
import os
import time

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {"Accept": "application/vnd.github.v3+json"}
if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"


def get_live_cncf_repos():
    """Stage 1: Fetch official CNCF projects directly from the GitHub source YAML."""
    print("Fetching CNCF landscape source directly from GitHub (Bypassing Cloudflare)...")

    try:
        response = requests.get("https://raw.githubusercontent.com/cncf/landscape/master/landscape.yml")
        if response.status_code != 200:
            print(f"Failed to fetch YAML: {response.status_code}")
            return {}

        landscape_data = yaml.safe_load(response.text)
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

                    if repo_url and repo_url.startswith("https://github.com/"):
                        repo_path = repo_url.replace("https://github.com/", "").strip("/").replace(".git", "")

                        repos[repo_path] = {
                            "tier": project_tier.capitalize(),
                            "lang": "Unknown"
                        }

    print(f"Discovered {len(repos)} official CNCF GitHub repositories.")
    return repos


def github_get(url, params=None, max_retries=3):
    """
    Wrapper around requests.get that retries on rate limit (403/429)
    instead of silently giving up. Respects GitHub's rate-limit reset header.
    """
    for attempt in range(max_retries):
        response = requests.get(url, headers=HEADERS, params=params)

        if response.status_code == 200:
            return response

        if response.status_code in (403, 429):
            remaining = response.headers.get("X-RateLimit-Remaining")
            reset_ts = response.headers.get("X-RateLimit-Reset")

            if remaining == "0" and reset_ts:
                wait_seconds = max(int(reset_ts) - int(time.time()), 1)
                print(f"Rate limited. Sleeping {wait_seconds}s until reset (attempt {attempt + 1}/{max_retries})...")
                time.sleep(min(wait_seconds, 60))  # cap wait so CI doesn't hang forever
            else:
                # Secondary rate limit or abuse detection - back off with exponential delay
                wait_seconds = 5 * (attempt + 1)
                print(f"Got {response.status_code}. Backing off {wait_seconds}s (attempt {attempt + 1}/{max_retries})...")
                time.sleep(wait_seconds)
            continue

        # Any other error (404, 500, etc.) - no point retrying, just report and stop
        print(f"Request failed: {response.status_code} for {url}")
        return response

    print(f"Giving up on {url} after {max_retries} retries.")
    return response


def fetch_issues(repos):
    """Stage 2: Hunt for beginner issues and repo metadata."""
    labels = ["good first issue", "good-first-issue", "help wanted"]
    all_issues = []

    for repo, meta in repos.items():
        print(f"Checking {repo} ({meta['tier']})...")

        repo_res = github_get(f"https://api.github.com/repos/{repo}")
        if repo_res.status_code == 200:
            meta["lang"] = repo_res.json().get("language") or "Unknown"

        for label in labels:
            url = f"https://api.github.com/repos/{repo}/issues"
            params = {"state": "open", "labels": label, "per_page": 30}

            response = github_get(url, params=params)

            if response.status_code == 200:
                issues = response.json()
                for issue in issues:
                    if "pull_request" not in issue:
                        issue_data = {
                            "repo": repo,
                            "tier": meta["tier"],
                            "lang": meta["lang"],
                            "title": issue["title"],
                            "url": issue["html_url"],
                            "labels": [l["name"] for l in issue["labels"]],
                            "created_at": issue["created_at"]
                        }
                        if issue_data not in all_issues:
                            all_issues.append(issue_data)

        time.sleep(0.5)

    with open("data.json", "w") as f:
        json.dump(all_issues, f, indent=4)
    print(f"\n--- SUCCESS ---")
    print(f"Saved {len(all_issues)} total beginner issues to data.json")


if __name__ == "__main__":
    cncf_repos = get_live_cncf_repos()

    if cncf_repos:
        fetch_issues(cncf_repos)
    else:
        print("No repositories found to scan. Halting process.")