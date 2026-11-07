"""Post or update one sticky tamperproof comment on a GitHub pull request.

    python -m tamperproof.github report.md

Runs inside GitHub Actions and reads GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_EVENT_PATH
and GITHUB_API_URL. Standard library only (urllib), so the action adds no dependency.

Why "sticky": an agent may push many times per PR. Editing one comment keeps the PR
readable and always shows the verdict for the latest head.

Why it never fails the job: the audit's exit code is the gate. A missing comment (fork
PRs get a read-only token, or the event is not a PR) is reported as a warning only.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

MARKER = "<!-- tamperproof -->"
PER_PAGE = 100

# (method, path, payload) -> parsed JSON. Injected so tests can fake the API.
Api = Callable[[str, str, "dict | None"], "dict | list"]


def comment_body(markdown: str) -> str:
    """The hidden marker lets later runs find this comment among human ones."""
    return f"{MARKER}\n{markdown}"


def find_comment(comments: list[dict]) -> int | None:
    """Id of our earlier comment, or None. Matches the marker, not the word 'tamperproof'."""
    for c in comments:
        if MARKER in (c.get("body") or ""):
            return c["id"]
    return None


def pr_number(event: dict) -> int | None:
    """PR number from a `pull_request` / `pull_request_target` event payload."""
    pr = event.get("pull_request")
    return pr["number"] if pr else None


def upsert_comment(repo: str, pr: int, markdown: str, api: Api) -> str:
    """Create or update the sticky comment. Returns 'created', 'updated' or 'forbidden'."""
    existing = None
    page = 1
    while existing is None:
        batch = api("GET", f"/repos/{repo}/issues/{pr}/comments?per_page={PER_PAGE}&page={page}", None)
        if not batch:
            break
        existing = find_comment(batch)
        page += 1
    payload = {"body": comment_body(markdown)}
    try:
        if existing is None:
            api("POST", f"/repos/{repo}/issues/{pr}/comments", payload)
            return "created"
        api("PATCH", f"/repos/{repo}/issues/comments/{existing}", payload)
        return "updated"
    except HTTPError as e:
        if e.code in (401, 403, 404):
            return "forbidden"
        raise


def http_api(base_url: str, token: str) -> Api:
    """Real GitHub REST client over urllib."""
    def call(method: str, path: str, payload: dict | None) -> dict | list:
        data = json.dumps(payload).encode() if payload is not None else None
        req = Request(base_url.rstrip("/") + path, data=data, method=method, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "tamperproof-action",
        })
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"null")
    return call


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m tamperproof.github report.md", file=sys.stderr)
        return 2
    env = os.environ
    event_path = env.get("GITHUB_EVENT_PATH")
    event = json.loads(Path(event_path).read_text()) if event_path else {}
    pr = pr_number(event)
    if pr is None:
        print("::notice::tamperproof: not a pull request event, no comment posted")
        return 0
    api = http_api(env.get("GITHUB_API_URL", "https://api.github.com"), env["GITHUB_TOKEN"])
    status = upsert_comment(env["GITHUB_REPOSITORY"], pr, Path(argv[0]).read_text(), api)
    if status == "forbidden":
        print("::warning::tamperproof: token cannot write PR comments (fork PR?); "
              "see the job summary for the report")
    else:
        print(f"tamperproof: PR comment {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
