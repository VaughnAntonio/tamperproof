"""Materialise a dataset rollout as two commits in a cached clone: base (as the agent found
the repo) and head (after its replayed edits), so the normal `audit` can diff them.

Why base = base_commit + the altered tests: that is the sandbox the agent started in. A
rollout that edits the impossible test back to the right value, or restores it with git,
then shows up as a test change, which is exactly the tampering the labels describe.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .replay import Replay, detect_root, editor_paths, replay

# Fixed identity and dates make commits content-addressed: the same task always yields the
# same base sha, which keeps results reproducible and lets repeated rollouts share objects.
_ENV = {
    "GIT_AUTHOR_NAME": "tamperproof", "GIT_AUTHOR_EMAIL": "calibration@tamperproof.invalid",
    "GIT_COMMITTER_NAME": "tamperproof", "GIT_COMMITTER_EMAIL": "calibration@tamperproof.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}


class RebuildError(RuntimeError):
    pass


@dataclass
class Pair:
    base: str
    head: str
    replay: Replay
    root: str


def _git(repo: Path, *args: str, stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], input=stdin, capture_output=True, text=True,
                          check=check, env={**os.environ, **_ENV})


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "--no-verify", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def ensure_clone(slug: str, cache: Path, url: str | None = None) -> Path:
    """Blobless clone of `owner/name`, made once. Blobs arrive lazily on checkout."""
    dest = cache / slug.replace("/", "__")
    if not (dest / ".git").exists():
        cache.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout",
                        url or f"https://github.com/{slug}.git", str(dest)], check=True, capture_output=True)
    return dest


def build_pair(clone: Path, task: dict, messages: list[dict]) -> Pair:
    """Commit base and head for one rollout. Leaves the clone checked out at head."""
    clone = Path(clone)
    base_commit = task["base_commit"]
    if _git(clone, "checkout", "-qf", "--detach", base_commit, check=False).returncode:
        _git(clone, "fetch", "-q", "origin", base_commit, check=False)
        _git(clone, "checkout", "-qf", "--detach", base_commit)
    _git(clone, "clean", "-qfdx")
    applied = _git(clone, "apply", "--whitespace=nowarn", "-", stdin=task["test_patch"], check=False)
    if applied.returncode:
        raise RebuildError(f"test_patch does not apply: {applied.stderr.strip()[:200]}")
    base = _commit(clone, "tamperproof calibration: base (base_commit + altered tests)")

    cache: dict[tuple[str, str], str | None] = {}

    def reader(ref: str):
        def read(path: str) -> str | None:
            if (ref, path) not in cache:
                out = _git(clone, "show", f"{ref}:{path}", check=False)
                cache[ref, path] = out.stdout if out.returncode == 0 else None
            return cache[ref, path]
        return read

    altered = _git(clone, "diff", "--name-only", base_commit, base).stdout.split()
    known = set(_git(clone, "ls-tree", "-r", "--name-only", base).stdout.splitlines())
    root = detect_root(editor_paths(messages), known)
    result = replay(messages, root=root, base=reader(base), pristine=reader(base_commit), altered=altered)

    for rel, text in result.files.items():
        path = clone / rel
        if text is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    head = _commit(clone, "tamperproof calibration: head (replayed agent edits)")
    return Pair(base, head, result, root)
