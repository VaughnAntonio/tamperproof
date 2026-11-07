"""Sticky PR comment logic (tamperproof/github.py) and SARIF details GitHub code scanning needs."""

import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from tamperproof.audit import AuditResult
from tamperproof.github import (
    MARKER,
    comment_body,
    find_comment,
    pr_number,
    upsert_comment,
)
from tamperproof.report import to_sarif
from tamperproof.static_rules import Finding


# ----- pure helpers ---------------------------------------------------------- #
def test_comment_body_carries_hidden_marker():
    body = comment_body("## ✅ tamperproof: PASS")
    assert body.startswith(MARKER)
    assert "## ✅ tamperproof: PASS" in body


def test_find_comment_returns_our_earlier_comment():
    comments = [
        {"id": 1, "body": "LGTM"},
        {"id": 2, "body": f"{MARKER}\n## old report"},
        {"id": 3, "body": "mentions tamperproof but has no marker"},
    ]
    assert find_comment(comments) == 2


def test_find_comment_none_when_absent():
    assert find_comment([{"id": 1, "body": "LGTM"}, {"id": 2, "body": None}]) is None


def test_pr_number_from_pull_request_event():
    assert pr_number({"pull_request": {"number": 7}}) == 7


def test_pr_number_none_outside_pull_requests():
    assert pr_number({"ref": "refs/heads/main"}) is None


# ----- upsert against a fake GitHub API ------------------------------------- #
class FakeApi:
    """Records calls; serves a fixed list of existing comments, paginated."""

    def __init__(self, existing, page_size=2, fail_writes=False):
        self.existing, self.page_size, self.fail_writes = existing, page_size, fail_writes
        self.calls = []

    def __call__(self, method, url, payload=None):
        self.calls.append((method, url, payload))
        if method == "GET":
            page = int(parse_qs(urlsplit(url).query)["page"][0])
            start = (page - 1) * self.page_size
            return self.existing[start:start + self.page_size]
        if self.fail_writes:
            raise HTTPError(url, 403, "Resource not accessible by integration", {}, None)
        return {"id": 99}


def test_upsert_creates_comment_when_none_exists():
    api = FakeApi([{"id": 1, "body": "LGTM"}])
    status = upsert_comment("o/r", 5, "report", api)
    assert status == "created"
    method, url, payload = api.calls[-1]
    assert method == "POST" and url.endswith("/repos/o/r/issues/5/comments")
    assert payload["body"].startswith(MARKER)


def test_upsert_updates_existing_comment_on_a_later_page():
    existing = [{"id": 1, "body": "a"}, {"id": 2, "body": "b"}, {"id": 3, "body": f"{MARKER}\nold"}]
    api = FakeApi(existing, page_size=2)
    status = upsert_comment("o/r", 5, "new report", api)
    assert status == "updated"
    method, url, payload = api.calls[-1]
    assert method == "PATCH" and url.endswith("/repos/o/r/issues/comments/3")
    assert "new report" in payload["body"]


def test_upsert_read_only_token_warns_instead_of_failing():
    # Fork PRs get a read-only GITHUB_TOKEN; the audit verdict must still decide the job.
    api = FakeApi([], fail_writes=True)
    assert upsert_comment("o/r", 5, "report", api) == "forbidden"


# ----- SARIF details code scanning cares about -------------------------------- #
def _result(line):
    f = Finding("TT01", "high", "tests/test_x.py", line, "`test_a` was deleted", {})
    return AuditResult(base="main", head="agent", findings=[f])


def test_sarif_start_line_is_at_least_one():
    # GitHub rejects a SARIF region with startLine 0.
    sarif = json.loads(to_sarif(_result(0)))
    region = sarif["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["region"]
    assert region["startLine"] == 1


def test_sarif_information_uri_points_at_real_repo():
    sarif = json.loads(to_sarif(_result(3)))
    assert sarif["runs"][0]["tool"]["driver"]["informationUri"] == \
        "https://github.com/VaughnAntonio/tamperproof"
