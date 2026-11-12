
import json
from pathlib import Path

from fetcher import get_live_cncf_repos

data_path = Path(__file__).with_name("data.json")

# Load the existing data first so we preserve all existing issues.
with data_path.open("r", encoding="utf-8") as file:
    issues = json.load(file)

if not isinstance(issues, list):
    raise SystemExit("ERROR: data.json must contain a JSON list.")

# This reads CNCF landscape metadata, not every GitHub issue.
repos = get_live_cncf_repos()

if not repos:
    raise SystemExit(
        "ERROR: Could not load CNCF landscape metadata. "
        "data.json was not changed."
    )

matched = 0
unmatched_repos = set()
ai_issues = 0

for issue in issues:
    repo_name = str(issue.get("repo", "")).strip().lower()
    metadata = repos.get(repo_name)

    if not metadata:
        unmatched_repos.add(repo_name)
        continue

    issue["category"] = metadata.get("category", "")
    issue["subcategory"] = metadata.get("subcategory", "")
    issue["description"] = metadata.get("description", "")
    issue["ai_related"] = bool(metadata.get("ai_related", False))

    matched += 1
    if issue["ai_related"]:
        ai_issues += 1

if matched == 0:
    raise SystemExit(
        "ERROR: No existing issues matched landscape repositories. "
        "data.json was not changed."
    )

# Write atomically so an interrupted run doesn't truncate data.json.
temp_path = data_path.with_suffix(".json.tmp")
with temp_path.open("w", encoding="utf-8") as file:
    json.dump(issues, file, indent=2, ensure_ascii=False)
    file.write("\n")

temp_path.replace(data_path)

print(f"Existing issues preserved: {len(issues)}")
print(f"Issues enriched with metadata: {matched}")
print(f"Issues marked AI Native: {ai_issues}")
print(f"Unmatched repository names: {len(unmatched_repos)}")

if unmatched_repos:
    print("Some repositories were not matched; their records were left unchanged.")
