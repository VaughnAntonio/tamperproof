"""Thin wrappers around git so the rest of the code works on (path, text) pairs."""

from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path


def git(repo: str | Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def changed_py_files(repo: str | Path, base: str, head: str) -> list[tuple[str, str | None, str | None]]:
    """Return (status, old_path, new_path) for every changed .py file."""
    out = git(repo, "diff", "--name-status", "-M", base, head)
    changes = []
    for line in out.splitlines():
        parts = line.split("\t")
        status = parts[0][0]
        if status == "R":
            old, new = parts[1], parts[2]
        elif status == "D":
            old, new = parts[1], None
        elif status == "A":
            old, new = None, parts[1]
        else:
            old = new = parts[1]
        if (old or new).endswith(".py"):
            changes.append((status, old, new))
    return changes


def read_at(repo: str | Path, ref: str, path: str | None) -> str | None:
    if path is None:
        return None
    try:
        return git(repo, "show", f"{ref}:{path}")
    except subprocess.CalledProcessError:
        return None


def all_py_files(repo: str | Path, ref: str) -> dict[str, str]:
    names = git(repo, "ls-tree", "-r", "--name-only", ref).splitlines()
    return {n: read_at(repo, ref, n) or "" for n in names if n.endswith(".py")}


def export_tree(repo: str | Path, ref: str, dest: str | Path) -> Path:
    """Write the full tree at `ref` into `dest` (no .git), for sandboxed test runs."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    data = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", ref],
        capture_output=True, check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")
    return dest
