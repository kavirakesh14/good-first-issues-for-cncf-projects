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
    
    # FIX: Added `or []` to protect against NoneType (null) values in the CNCF YAML
    for category in landscape_data.get("landscape") or []:
        for subcategory in category.get("subcategories") or []:
            for item in subcategory.get("items") or []:
                
                project_tier = item.get("project")
                # We only care about official CNCF projects
                if project_tier in ["graduated", "incubating", "sandbox"]:
                    repo_url = item.get("repo_url", "")
                    
                    if repo_url and repo_url.startswith("https://github.com/"):
                        # Clean the URL to get just "org/repo"
                        repo_path = repo_url.replace("https://github.com/", "").strip("/").replace(".git", "")
                        
                        repos[repo_path] = {
                            "tier": project_tier.capitalize(),
                            "lang": "Unknown" # We will fetch this directly from the GitHub API below
                        }
                            
    print(f"Discovered {len(repos)} official CNCF GitHub repositories.")
    return repos


def fetch_issues(repos):
    """Stage 2: Hunt for beginner issues and repo metadata."""
    labels = ["good first issue", "good-first-issue", "help wanted"]
    all_issues = []

    for repo, meta in repos.items():
        print(f"Checking {repo} ({meta['tier']})...")
        
        # Fetch repo details to get the primary programming language for the frontend filter
        repo_res = requests.get(f"https://api.github.com/repos/{repo}", headers=HEADERS)
        if repo_res.status_code == 200:
             meta["lang"] = repo_res.json().get("language") or "Unknown"
        elif repo_res.status_code == 403:
             print("Rate limited by GitHub! Pausing briefly...")
             time.sleep(5)
        
        # Fetch the actual issues
        for label in labels:
            url = f"https://api.github.com/repos/{repo}/issues"
            params = {"state": "open", "labels": label, "per_page": 30}
            
            response = requests.get(url, headers=HEADERS, params=params)
            
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
            elif response.status_code == 403:
                time.sleep(5)
                
        # Protect the GitHub API Limit
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
