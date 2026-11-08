"""Thin wrappers around git so the rest of the code works on (path, text) pairs."""

from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path


def git(repo: str | Path, *args: str) -> str:
    # quotePath=false: by default git prints `"test_caf\303\251.py"` for non-ASCII names, which
    # matches no suffix and no path, so tampering in such a file would go unseen.
    return subprocess.run(
        ["git", "-c", "core.quotePath=false", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    ).stdout


def changed_py_files(repo: str | Path, base: str, head: str) -> list[tuple[str, str | None, str | None]]:
    """Return (status, old_path, new_path) for every changed .py file."""
    return changed_files(repo, base, head, (".py",))


def changed_files(repo: str | Path, base: str, head: str,
                  suffixes: tuple[str, ...]) -> list[tuple[str, str | None, str | None]]:
    """Return (status, old_path, new_path) for every changed file ending in `suffixes`."""
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
        if (old or new).endswith(suffixes):
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
    """Every .py file at `ref`, read through one `git cat-file --batch` process.

    One `git show` per file meant ~2,700 processes per audit on a repo like django.
    """
    paths: dict[str, list[str]] = {}  # blob sha -> paths (identical files share one blob)
    for line in git(repo, "ls-tree", "-r", ref).splitlines():
        meta, path = line.split("\t", 1)
        _mode, kind, sha = meta.split()
        if kind == "blob" and path.endswith(".py"):
            paths.setdefault(sha, []).append(path)
    out = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"],
                         input="".join(f"{sha}\n" for sha in paths).encode(),
                         capture_output=True, check=True).stdout
    files, pos = {}, 0
    while pos < len(out):
        end = out.index(b"\n", pos)
        header = out[pos:end].decode().split()  # "<sha> blob <size>" or "<sha> missing"
        pos = end + 1
        if header[1] == "missing":
            continue
        size = int(header[2])
        text = out[pos:pos + size].decode("utf-8", errors="replace")
        files.update(dict.fromkeys(paths[header[0]], text))
        pos += size + 1  # content is followed by one newline
    return files


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
