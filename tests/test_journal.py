import importlib.util
import re
from datetime import date
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
ENTRY_HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2})", re.MULTILINE)


def load_journal():
    spec = importlib.util.spec_from_file_location(
        "journal", REPOSITORY_ROOT / "scripts" / "journal.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


journal = load_journal()


def entry_for(day, count=1):
    noun = "contribution" if count == 1 else "contributions"
    return f"## {day} (Day)\n\n{count} {noun}.\n\n**Public**\n\n- No public activity.\n"


def headings(text):
    return ENTRY_HEADING.findall(text)


def write_entries(path, days):
    for day in days:
        journal.upsert(path, date.fromisoformat(day), entry_for(day))
    return path.read_text()


def test_a_new_journal_starts_with_the_header_for_its_year(tmp_path):
    path = tmp_path / "2026" / "journal.md"
    write_entries(path, ["2026-08-25"])
    text = path.read_text()
    assert text.startswith("# Journal 2026")
    assert "recorded only as a count" in text


def test_each_date_gets_exactly_one_section(tmp_path):
    path = tmp_path / "2026" / "journal.md"
    text = write_entries(path, ["2026-08-25", "2026-08-26", "2026-08-27"])
    assert headings(text) == ["2026-08-25", "2026-08-26", "2026-08-27"]


def test_rewriting_a_date_replaces_it_instead_of_duplicating_it(tmp_path):
    path = tmp_path / "2026" / "journal.md"
    write_entries(path, ["2026-08-25", "2026-08-26"])
    journal.upsert(path, date(2026, 8, 25), entry_for("2026-08-25", count=99))
    text = path.read_text()
    assert headings(text) == ["2026-08-25", "2026-08-26"]
    assert "99 contributions." in text
    assert text.count("## 2026-08-25") == 1


def test_replacing_an_entry_leaves_its_neighbours_untouched(tmp_path):
    path = tmp_path / "2026" / "journal.md"
    write_entries(path, ["2026-08-25", "2026-08-26", "2026-08-27"])
    before = path.read_text()
    journal.upsert(path, date(2026, 8, 26), entry_for("2026-08-26", count=7))
    after = path.read_text()
    for day in ("2026-08-25", "2026-08-27"):
        assert entry_for(day).strip() in before
        assert entry_for(day).strip() in after


def test_rerunning_the_same_dates_is_idempotent(tmp_path):
    path = tmp_path / "2026" / "journal.md"
    first = write_entries(path, ["2026-08-25", "2026-08-26"])
    second = write_entries(path, ["2026-08-25", "2026-08-26"])
    assert first == second


def test_the_file_ends_with_exactly_one_newline(tmp_path):
    path = tmp_path / "2026" / "journal.md"
    text = write_entries(path, ["2026-08-25", "2026-08-26"])
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_the_window_spans_one_whole_ist_day_expressed_in_utc():
    start, end = journal.utc_window(date(2026, 9, 2))
    assert start == "2026-09-01T18:30:00Z"
    assert end == "2026-09-02T18:29:59Z"


def test_a_private_repository_is_never_named():
    assert (
        journal.public_repository_name({"repository": {"isPrivate": True, "nameWithOwner": "o/p"}})
        is None
    )
    assert journal.public_repository_name({"repository": {"nameWithOwner": "o/p"}}) is None
    assert journal.public_repository_name({}) is None
    assert (
        journal.public_repository_name({"repository": {"isPrivate": False, "nameWithOwner": "o/p"}})
        == "o/p"
    )


def test_naming_a_repository_that_was_not_proven_public_is_refused():
    with pytest.raises(SystemExit):
        journal.assert_nothing_private_is_named(
            {"named_repositories": ["owner/secret"], "proven_public": set()}
        )


def test_naming_a_repository_that_was_proven_public_is_allowed():
    journal.assert_nothing_private_is_named(
        {"named_repositories": ["owner/open"], "proven_public": {"owner/open"}}
    )


def test_private_work_is_rendered_as_a_bare_count():
    rendered = journal.render(
        date(2026, 9, 2),
        {
            "total": 5,
            "private_total": 4,
            "commit_repositories": [],
            "opened_pull_requests": [],
            "reviewed_pull_requests": [],
            "opened_issues": [],
            "named_repositories": [],
            "proven_public": set(),
        },
    )
    assert "## 2026-09-02 (Wednesday)" in rendered
    assert "5 contributions." in rendered
    assert "4 contributions across private repositories." in rendered
    assert "- No public activity." in rendered


def test_a_day_with_nothing_on_it_says_so():
    rendered = journal.render(
        date(2026, 9, 2),
        {
            "total": 0,
            "private_total": 0,
            "commit_repositories": [],
            "opened_pull_requests": [],
            "reviewed_pull_requests": [],
            "opened_issues": [],
            "named_repositories": [],
            "proven_public": set(),
        },
    )
    assert "No contributions." in rendered
    assert "**Private**" not in rendered


def test_long_commit_lists_are_truncated_with_a_remainder_line():
    subjects = [f"fix(x): change number {index}" for index in range(15)]
    rendered = journal.render(
        date(2026, 9, 2),
        {
            "total": 15,
            "private_total": 0,
            "commit_repositories": [{"repository": "o/p", "count": 15, "subjects": subjects}],
            "opened_pull_requests": [],
            "reviewed_pull_requests": [],
            "opened_issues": [],
            "named_repositories": ["o/p"],
            "proven_public": {"o/p"},
        },
    )
    assert rendered.count("  - fix(x): change number") == journal.MAX_LISTED_SUBJECTS
    assert "and 5 more commits" in rendered


def test_plural_only_adds_an_s_when_it_should():
    assert journal.plural(1, "contribution") == "1 contribution"
    assert journal.plural(0, "contribution") == "0 contributions"
    assert journal.plural(2, "contribution") == "2 contributions"
