#!/usr/bin/env python3
"""Write one daily activity entry into the year's journal file."""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
LOGIN = "vineethkrishnan"
SELF_REPOSITORY = "vineethkrishnan/vineethkrishnan"
JOURNAL_COMMIT_PREFIX = "docs(journal):"
MAX_LISTED_SUBJECTS = 10
API_ROOT = "https://api.github.com"
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

JOURNAL_HEADER = """# Journal {year}

A daily log of GitHub activity, written automatically by `scripts/journal.py`.

Work done in private repositories is recorded only as a count. No private repository, organisation, branch, pull request, or commit message is ever named here.
"""

CONTRIBUTIONS_QUERY = """
query($from: DateTime!, $to: DateTime!) {
  viewer {
    contributionsCollection(from: $from, to: $to) {
      restrictedContributionsCount
      totalCommitContributions
      totalPullRequestContributions
      totalPullRequestReviewContributions
      totalIssueContributions
      commitContributionsByRepository(maxRepositories: 100) {
        repository { nameWithOwner isPrivate }
        contributions { totalCount }
      }
      pullRequestContributions(first: 100) {
        nodes { pullRequest { number title url repository { nameWithOwner isPrivate } } }
      }
      pullRequestReviewContributions(first: 100) {
        nodes { pullRequest { number title url repository { nameWithOwner isPrivate } } }
      }
      issueContributions(first: 100) {
        nodes { issue { number title url repository { nameWithOwner isPrivate } } }
      }
    }
  }
}
"""


def request_json(url, token, method="GET", payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", f"{LOGIN}-journal")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def current_ist_date():
    return datetime.now(IST).date()


def utc_window(target_date):
    start = datetime.combine(target_date, time.min, tzinfo=IST)
    end = start + timedelta(days=1) - timedelta(seconds=1)
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    return start.astimezone(timezone.utc).strftime(stamp), end.astimezone(timezone.utc).strftime(stamp)


def fetch_contributions(token, window_start, window_end):
    result = request_json(
        f"{API_ROOT}/graphql",
        token,
        method="POST",
        payload={"query": CONTRIBUTIONS_QUERY, "variables": {"from": window_start, "to": window_end}},
    )
    if result.get("errors"):
        raise SystemExit(f"GraphQL error: {json.dumps(result['errors'])}")
    return result["data"]["viewer"]["contributionsCollection"]


def public_repository_name(record):
    repository = (record or {}).get("repository") or {}
    if repository.get("isPrivate") is not False:
        return None
    return repository.get("nameWithOwner")


def fetch_commit_subjects(token, repository, window_start, window_end):
    query = urllib.parse.urlencode(
        {"author": LOGIN, "since": window_start, "until": window_end, "per_page": 100}
    )
    try:
        commits = request_json(f"{API_ROOT}/repos/{repository}/commits?{query}", token)
    except urllib.error.HTTPError as error:
        if error.code in (404, 409):
            return [], 0
        raise

    subjects = []
    journal_commits = 0
    for commit in commits:
        subject = commit["commit"]["message"].split("\n")[0].strip()
        if repository == SELF_REPOSITORY and subject.startswith(JOURNAL_COMMIT_PREFIX):
            journal_commits += 1
            continue
        subjects.append(subject)
    return subjects, journal_commits


def collect(token, collection, window_start, window_end):
    proven_public = set()
    named_repositories = []
    journal_commits = 0
    commit_repositories = []
    public_commits = 0

    for record in collection["commitContributionsByRepository"]:
        count = record["contributions"]["totalCount"]
        name = public_repository_name(record)
        if not name:
            continue
        public_commits += count
        proven_public.add(name)
        subjects, skipped = fetch_commit_subjects(token, name, window_start, window_end)
        journal_commits += skipped
        listed_count = max(count - skipped, 0)
        if listed_count == 0 and not subjects:
            continue
        named_repositories.append(name)
        commit_repositories.append({"repository": name, "count": listed_count, "subjects": subjects})

    def collect_items(nodes, key):
        items = []
        for node in nodes:
            item = (node or {}).get(key)
            name = public_repository_name(item)
            if not name:
                continue
            proven_public.add(name)
            named_repositories.append(name)
            items.append(
                {
                    "repository": name,
                    "number": item["number"],
                    "title": item["title"],
                    "url": item["url"],
                }
            )
        return items

    opened_pull_requests = collect_items(collection["pullRequestContributions"]["nodes"], "pullRequest")
    reviewed_pull_requests = collect_items(
        collection["pullRequestReviewContributions"]["nodes"], "pullRequest"
    )
    opened_issues = collect_items(collection["issueContributions"]["nodes"], "issue")

    private_total = collection["restrictedContributionsCount"]
    private_total += max(collection["totalCommitContributions"] - public_commits, 0)
    private_total += max(
        collection["totalPullRequestContributions"] - len(opened_pull_requests), 0
    )
    private_total += max(
        collection["totalPullRequestReviewContributions"] - len(reviewed_pull_requests), 0
    )
    private_total += max(collection["totalIssueContributions"] - len(opened_issues), 0)

    reported_total = (
        collection["totalCommitContributions"]
        + collection["totalPullRequestContributions"]
        + collection["totalPullRequestReviewContributions"]
        + collection["totalIssueContributions"]
        + collection["restrictedContributionsCount"]
    )
    total = max(reported_total - journal_commits, 0)

    return {
        "total": total,
        "private_total": private_total,
        "commit_repositories": commit_repositories,
        "opened_pull_requests": opened_pull_requests,
        "reviewed_pull_requests": reviewed_pull_requests,
        "opened_issues": opened_issues,
        "named_repositories": named_repositories,
        "proven_public": proven_public,
    }


def assert_nothing_private_is_named(activity):
    for name in activity["named_repositories"]:
        if name not in activity["proven_public"]:
            raise SystemExit(f"refusing to write: {name} was not proven public")


def plural(count, noun):
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def render(target_date, activity):
    lines = [f"## {target_date.isoformat()} ({target_date.strftime('%A')})", ""]

    total = activity["total"]
    lines.append(f"{plural(total, 'contribution')}." if total else "No contributions.")
    lines += ["", "**Public**", ""]

    public_lines = []
    for entry in activity["commit_repositories"]:
        public_lines.append(f"- `{entry['repository']}` - {plural(entry['count'], 'commit')}")
        for subject in entry["subjects"][:MAX_LISTED_SUBJECTS]:
            public_lines.append(f"  - {subject}")
        remaining = len(entry["subjects"]) - MAX_LISTED_SUBJECTS
        if remaining > 0:
            public_lines.append(f"  - and {plural(remaining, 'more commit')}")

    for entry in activity["opened_pull_requests"]:
        public_lines.append(
            f"- Opened [#{entry['number']} {entry['title']}]({entry['url']}) in `{entry['repository']}`"
        )
    for entry in activity["reviewed_pull_requests"]:
        public_lines.append(
            f"- Reviewed [#{entry['number']} {entry['title']}]({entry['url']}) in `{entry['repository']}`"
        )
    for entry in activity["opened_issues"]:
        public_lines.append(
            f"- Opened issue [#{entry['number']} {entry['title']}]({entry['url']}) in `{entry['repository']}`"
        )

    lines += public_lines if public_lines else ["- No public activity."]

    if activity["private_total"]:
        lines += [
            "",
            "**Private**",
            "",
            f"- {plural(activity['private_total'], 'contribution')} across private repositories.",
        ]

    return "\n".join(lines) + "\n"


def upsert(path, target_date, entry):
    if path.exists():
        existing = path.read_text()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = JOURNAL_HEADER.format(year=target_date.year)

    heading = re.escape(f"## {target_date.isoformat()}")
    section = re.compile(rf"^{heading}[^\n]*\n(?:(?!## )[^\n]*\n)*", re.MULTILINE)
    if section.search(existing):
        updated = section.sub(lambda _: entry + "\n", existing, count=1)
    else:
        updated = insert_in_date_order(existing, target_date, entry)

    path.write_text(updated.rstrip() + "\n")


ENTRY_HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2})", re.MULTILINE)


def insert_in_date_order(existing, target_date, entry):
    for match in ENTRY_HEADING.finditer(existing):
        if match.group(1) > target_date.isoformat():
            return existing[: match.start()].rstrip() + "\n\n" + entry + "\n" + existing[match.start() :]
    return existing.rstrip() + "\n\n" + entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="IST date to log, YYYY-MM-DD (default: today in IST)")
    parser.add_argument("--dry-run", action="store_true", help="print the entry instead of writing it")
    arguments = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN is not set")

    target_date = date.fromisoformat(arguments.date) if arguments.date else current_ist_date()
    window_start, window_end = utc_window(target_date)

    collection = fetch_contributions(token, window_start, window_end)
    activity = collect(token, collection, window_start, window_end)
    assert_nothing_private_is_named(activity)
    entry = render(target_date, activity)

    if arguments.dry_run:
        sys.stdout.write(entry)
        return

    path = REPOSITORY_ROOT / str(target_date.year) / "journal.md"
    upsert(path, target_date, entry)
    print(f"wrote {target_date.isoformat()} to {path.relative_to(REPOSITORY_ROOT)}")

    step_output = os.environ.get("GITHUB_OUTPUT")
    if step_output:
        with open(step_output, "a") as handle:
            handle.write(f"date={target_date.isoformat()}\n")


if __name__ == "__main__":
    main()
