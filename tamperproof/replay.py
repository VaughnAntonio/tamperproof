"""Rebuild an agent's final file tree from its transcript, without executing anything.

Why replay instead of run: the rollouts are untrusted RL-agent shell commands and need each
project's sandbox. Editor operations, heredoc writes and git restores are deterministic and
can be applied as text edits. Anything else that writes a `.py` file (sed -i, patch, cp, a
Python `open(..., "w")`) is *not* guessed at: the rollout is marked `partial` and the caller
leaves it out of the headline numbers. That keeps every reconstructed tree exact.
"""

from __future__ import annotations

import json
import posixpath
import re
import shlex
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

Reader = Callable[[str], "str | None"]

EDITOR = "str_replace_editor"
_IGNORABLE_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".tox", ".eggs", ".hypothesis"}

_HEREDOC = re.compile(
    r"cat\s+(?:>\s*(?P<t1>[^\s<;&|]+)\s*<<-?\s*(?P<q1>['\"]?)(?P<d1>\w+)(?P=q1)"
    r"|<<-?\s*(?P<q2>['\"]?)(?P<d2>\w+)(?P=q2)\s*>\s*(?P<t2>[^\s;&|]+))"
    # The closing line must be the delimiter that opened this heredoc, whichever form it was.
    r"[^\n]*\n(?P<body>.*?)\n(?(d1)(?P=d1)|(?P=d2))[ \t]*(?=\n|$)",
    re.S,
)
_SEGMENT = re.compile(r"&&|\|\||;|\n|\|")
_REDIRECT = re.compile(r"(?<![<>=\-])>{1,2}\s*(?!&)([^\s;&|<>()'\"]+)")
_PY_WRITE = re.compile(
    r"open\([^)]*['\"][wax]b?\+?['\"]|\.write_text\(|\.write_bytes\(|os\.(?:remove|unlink|rename|replace)\("
    r"|shutil\.(?:copy\w*|move|rmtree)\("
)


@dataclass
class Replay:
    files: dict[str, str | None]  # repo-relative path -> final text (None = deleted); only changed paths
    partial: list[str] = field(default_factory=list)  # why the tree may be inexact
    applied: int = 0  # write operations replayed


def _args(call: dict) -> dict:
    fn = call.get("function") or {}
    raw = call.get("arguments", fn.get("arguments", "{}"))
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return out if isinstance(out, dict) else {}


def _name(call: dict) -> str:
    return call.get("name") or (call.get("function") or {}).get("name") or ""


def _rejected(reply: str | None) -> bool:
    """A tool reply that says the write did not happen. Such writes must not be replayed."""
    if reply is None:
        return False
    try:
        data = json.loads(reply)
    except (json.JSONDecodeError, TypeError):
        data = {"output": reply}
    if isinstance(data, dict):
        if data.get("error"):
            return True
        out = data.get("output")
        if isinstance(out, str) and (out.lstrip().upper().startswith("ERROR")
                                     or "No replacement was performed" in out):
            return True
    return False


def tool_calls(messages: Iterable[dict]) -> list[tuple[str, dict, str | None]]:
    """(tool name, arguments, reply content) for every call, in order."""
    msgs = list(messages)
    replies = {m.get("tool_call_id"): m.get("content") for m in msgs if m.get("role") == "tool"}
    out = []
    for m in msgs:
        if m.get("role") != "assistant":
            continue
        for c in m.get("tool_calls") or []:
            out.append((_name(c), _args(c), replies.get(c.get("id"))))
    return out


def detect_root(paths: Iterable[str], known: set[str], default: str = "/testbed") -> str:
    """Sandbox repo root, by majority vote of absolute paths that end in a known repo file."""
    votes: dict[str, int] = {}
    for p in paths:
        if not p.startswith("/"):
            continue
        for k in known:
            if p.endswith("/" + k):
                root = p[: -len(k) - 1] or "/"
                votes[root] = votes.get(root, 0) + 1
    return max(votes, key=lambda r: (votes[r], -len(r))) if votes else default


def editor_paths(messages: Iterable[dict]) -> list[str]:
    return [a["path"] for n, a, _ in tool_calls(messages) if n == EDITOR and isinstance(a.get("path"), str)]


class _Tree:
    def __init__(self, root: str, base: Reader, pristine: Reader, altered: Iterable[str]) -> None:
        self.root = posixpath.normpath(root)
        self.base, self.pristine = base, pristine
        self.state: dict[str, str | None] = {}
        self.touched: set[str] = set(altered)
        self.history: dict[str, list[str | None]] = {}
        self.stash: list[dict[str, str | None]] = []
        self.cwd = self.root
        self.result = Replay(files={})

    # paths ------------------------------------------------------------------- #
    def rel(self, path: str, cwd: str | None = None) -> str | None:
        """Repo-relative path, or None when the path is outside the repo."""
        p = posixpath.normpath(posixpath.join(cwd or self.cwd, path))
        if p == self.root:
            return ""
        return p[len(self.root) + 1:] if p.startswith(self.root + "/") else None

    def text(self, rel: str) -> str | None:
        return self.state[rel] if rel in self.state else self.base(rel)

    def set(self, rel: str, text: str | None, undoable: bool = False) -> None:
        if undoable:
            self.history.setdefault(rel, []).append(self.text(rel))
        self.state[rel] = text
        self.touched.add(rel)
        self.result.applied += 1

    def partial(self, why: str) -> None:
        self.result.partial.append(why)

    # git ---------------------------------------------------------------------- #
    def tracked(self, prefix: str = "") -> list[str]:
        return [p for p in sorted(self.touched)
                if (not prefix or p == prefix or p.startswith(prefix.rstrip("/") + "/"))
                and self.pristine(p) is not None]

    def restore(self, rels: Iterable[str]) -> None:
        for r in rels:
            for p in self.tracked(r):
                self.set(p, self.pristine(p))

    def git(self, tok: list[str], cwd: str) -> None:
        sub, rest = (tok[1], tok[2:]) if len(tok) > 1 else ("", [])
        flags = [t for t in rest if t.startswith("-")]
        if sub == "checkout":
            if "-b" in flags or "-B" in flags:
                return
            if "--" in rest:
                i = rest.index("--")
                ish, paths = [t for t in rest[:i] if not t.startswith("-")], rest[i + 1:]
            else:
                args = [t for t in rest if not t.startswith("-")]
                ish = args[:1] if args[:1] == ["HEAD"] else []
                paths = args[len(ish):]
            if ish and ish != ["HEAD"]:
                return self.partial(f"bash:git checkout {' '.join(ish)} -- (other commit)")
            rels = [r for r in (self.rel(p, cwd) for p in paths) if r is not None]
            self.restore(rels)
        elif sub == "restore":
            if "--staged" in flags and "--worktree" not in flags and "-W" not in flags:
                return
            if any(f.startswith("--source") and f not in ("--source=HEAD",) for f in flags) or "-s" in flags:
                return self.partial("bash:git restore --source (other commit)")
            paths = [t for t in rest if not t.startswith("-")]
            self.restore(r for r in (self.rel(p, cwd) for p in paths) if r is not None)
        elif sub == "reset" and "--hard" in flags:
            others = [t for t in rest if not t.startswith("-") and t != "HEAD"]
            if others:
                return self.partial("bash:git reset --hard <other commit>")
            self.restore([""])
        elif sub == "stash":
            action = rest[0] if rest and not rest[0].startswith("-") else "push"
            if action in ("push", "save"):
                saved = {p: self.text(p) for p in self.tracked() if self.text(p) != self.pristine(p)}
                self.stash.append(saved)
                self.restore(saved)
            elif action in ("pop", "apply"):
                if not self.stash:
                    return self.partial("bash:git stash pop with no recorded stash")
                saved = self.stash.pop() if action == "pop" else self.stash[-1]
                for p, t in saved.items():
                    self.set(p, t)
            elif action == "drop" and self.stash:
                self.stash.pop()
        elif sub in ("apply", "am", "cherry-pick", "merge", "rebase", "revert", "clean", "pull", "mv", "rm"):
            if sub == "apply" and ("--check" in flags or "--stat" in flags):
                return
            self.partial(f"bash:git {sub}")

    # shell writes ------------------------------------------------------------- #
    def _matters(self, rel: str | None, recursive: bool = False) -> bool:
        """Could writing this path change what tamperproof audits?"""
        if rel is None:  # outside the repo (/tmp, /dev/null, ...)
            return False
        parts = rel.split("/")
        if _IGNORABLE_PARTS & set(parts) or rel.endswith((".pyc", ".egg-info")):
            return False
        if rel.endswith(".py"):
            return True
        return recursive and "." not in parts[-1]  # rm -r / cp -r on a directory

    def segment(self, seg: str, heredocs: dict[str, tuple[str, str]]) -> None:
        seg = seg.strip()
        if not seg:
            return
        if seg in heredocs:
            target, body = heredocs[seg]
            rel = self.rel(target)
            if rel is not None:
                self.set(rel, body + "\n")
            return
        try:
            tok = shlex.split(seg, comments=True)
        except ValueError:
            tok = seg.split()
        if not tok:
            return
        cmd = posixpath.basename(tok[0])
        if cmd == "cd":
            self.cwd = posixpath.normpath(posixpath.join(self.cwd, tok[1] if len(tok) > 1 else self.root))
            return
        if cmd == "git":
            return self.git(tok, self.cwd)
        args = [t for t in tok[1:] if not t.startswith("-")]
        flags = "".join(t for t in tok[1:] if t.startswith("-") and not t.startswith("--"))
        if cmd in ("patch",):
            return self.partial("bash:patch")
        writes = {
            "sed": "i" in flags or "--in-place" in tok,
            "perl": "i" in flags,
            "tee": True, "cp": True, "mv": True, "rm": True, "truncate": True, "touch": True,
        }
        if writes.get(cmd):
            recursive = "r" in flags.lower() or cmd == "mv"
            for a in args:
                rel = self.rel(a)
                if self._matters(rel, recursive):
                    return self.partial(f"bash:{cmd}{' -i' if cmd == 'sed' else ''}:{rel}")
        for m in _REDIRECT.finditer(seg):
            rel = self.rel(m.group(1))
            if self._matters(rel):
                return self.partial(f"bash:redirect:{rel}")

    def bash(self, command: str) -> None:
        heredocs: dict[str, tuple[str, str]] = {}

        def hold(m: re.Match) -> str:
            key = f"__TAMPERPROOF_HEREDOC_{len(heredocs)}__"
            heredocs[key] = (m.group("t1") or m.group("t2"), m.group("body"))
            return "\n" + key + "\n"

        rest = _HEREDOC.sub(hold, command)
        if _PY_WRITE.search(rest) and ".py" in rest:
            self.partial("bash:python file write")
            return
        for seg in _SEGMENT.split(rest):
            self.segment(seg, heredocs)

    # editor ------------------------------------------------------------------- #
    def editor(self, a: dict) -> None:
        cmd, path = a.get("command"), a.get("path")
        if cmd == "view" or not isinstance(path, str):
            return
        rel = self.rel(path)
        if rel is None:
            return
        cur = self.text(rel)
        if cmd == "create":
            self.set(rel, a.get("file_text") or "", undoable=True)
        elif cmd == "str_replace":
            old, new = a.get("old_str") or "", a.get("new_str") or ""
            if cur is None or not old or cur.count(old) != 1:
                return self.partial(f"editor:str_replace diverged:{rel}")
            self.set(rel, cur.replace(old, new, 1), undoable=True)
        elif cmd == "insert":
            n, new = a.get("insert_line"), a.get("new_str") or ""
            lines = (cur or "").splitlines(keepends=True)
            if cur is None or not isinstance(n, int) or not 0 <= n <= len(lines):
                return self.partial(f"editor:insert diverged:{rel}")
            self.set(rel, "".join(lines[:n] + [new if new.endswith("\n") else new + "\n"] + lines[n:]),
                     undoable=True)
        elif cmd == "undo_edit":
            stack = self.history.get(rel)
            if not stack:
                return self.partial(f"editor:undo_edit with no history:{rel}")
            self.state[rel] = stack.pop()
            self.result.applied += 1
        else:
            self.partial(f"editor:unknown command {cmd!r}")


def replay(messages: Iterable[dict], *, root: str, base: Reader, pristine: Reader,
           altered: Iterable[str] = ()) -> Replay:
    """Apply every accepted write in `messages` to the tree the agent started from.

    `base` reads the starting tree (base_commit + the altered tests), `pristine` reads
    base_commit itself (what a git restore brings back), and `altered` lists the paths
    the test patch touched, so `git reset --hard` knows to revert them too.
    """
    tree = _Tree(root, base, pristine, altered)
    for name, args, reply in tool_calls(messages):
        if _rejected(reply):
            continue
        if name == EDITOR:
            tree.editor(args)
        elif name == "bash" and isinstance(args.get("command"), str):
            tree.bash(args["command"])
        elif name == "python" and isinstance(args.get("code"), str):
            if _PY_WRITE.search(args["code"]) and ".py" in args["code"]:
                tree.partial("python:file write")
    tree.result.files = {p: t for p, t in tree.state.items() if t != base(p)}
    return tree.result
