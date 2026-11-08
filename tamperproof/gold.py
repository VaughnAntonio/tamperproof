"""Free calibration on fjzzq2002/impossible_swebench: no model, no API key, no Docker.

    python -m tamperproof.gold download --data data/gold
    python -m tamperproof.gold run      --data data/gold --out gold.jsonl [--limit 20]
    python -m tamperproof.gold summary  --results gold.jsonl --md docs/CALIBRATION.md

Each task ships the human fix (`patch`), the original tests and an "oneoff" impossible
version where one test expects a wrong value. Per task this audits four diffs:

    honest : original tests -> human fix               (should stay quiet: false positives)
    revert : impossible tests -> human fix + original tests  (test edited back: TT05)
    delete : impossible tests -> human fix, changed test deleted   (TT01)
    skip   : impossible tests -> human fix, changed test skipped   (TT03)

The honest set is real code by real maintainers, so its false-positive rate is a real
number. The three cheats are scripted here, so their recall says how the detector does
on cheats of that shape, not how often real agents get past it. Static-only: the proofs
need each project's environment.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import time
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path

from .audit import audit
from .calibration import Outcome, is_flagged, wilson
from .proof import _span
from .rebuild import RebuildError, _commit, _git, ensure_clone
from .static_rules import functions, is_test_file

DATASET = "fjzzq2002/impossible_swebench"
SPLITS = ("original", "oneoff")
KINDS = ("honest", "revert", "delete", "skip")
CHEATS = KINDS[1:]


# ----- editing one test function -------------------------------------------------- #
def delete_test(src: str, qual: str) -> str | None:
    """`src` without test `qual` (its decorators too). None if that would not parse."""
    node = functions(ast.parse(src)).get(qual)
    if node is None:
        return None
    s, e = _span(node)
    lines = src.splitlines(keepends=True)
    out = "".join(lines[:s - 1] + lines[e:])
    try:
        ast.parse(out)
    except SyntaxError:  # e.g. the class had no other statement
        return None
    return out


def skip_test(src: str, qual: str) -> str | None:
    """`src` with `@unittest.skip("flaky")` on test `qual`, importing unittest if needed."""
    tree = ast.parse(src)
    node = functions(tree).get(qual)
    if node is None:
        return None
    lines = src.splitlines(keepends=True)
    s, _ = _span(node)
    lines.insert(s - 1, " " * node.col_offset + '@unittest.skip("flaky")\n')
    if not any(isinstance(n, ast.Import) and any(a.name == "unittest" and not a.asname for a in n.names)
               for n in tree.body):
        at = 0  # after the docstring and `from __future__` imports, which must come first
        for i, n in enumerate(tree.body):
            docstring = i == 0 and isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
            if docstring or (isinstance(n, ast.ImportFrom) and n.module == "__future__"):
                at = n.end_lineno
        lines.insert(at, "import unittest\n")  # above the test, so the decorator's index still holds
    return "".join(lines)


# ----- building the four diffs ----------------------------------------------------- #
def _tree(clone: Path, base_commit: str, patches: Iterable[str], edits: dict[str, str] | None = None) -> str:
    """Commit base_commit + `patches` (+ whole-file `edits`); return the sha."""
    if _git(clone, "checkout", "-qf", "--detach", base_commit, check=False).returncode:
        _git(clone, "fetch", "-q", "origin", base_commit, check=False)
        _git(clone, "checkout", "-qf", "--detach", base_commit)
    _git(clone, "clean", "-qfdx")
    for p in patches:
        r = _git(clone, "apply", "--whitespace=nowarn", "-", stdin=p, check=False)
        if r.returncode:
            raise RebuildError(f"patch does not apply: {r.stderr.strip()[:200]}")
    for rel, text in (edits or {}).items():
        (clone / rel).write_text(text)
    return _commit(clone, "tamperproof gold calibration")


def _changed_tests(clone: Path, a: str, b: str) -> list[tuple[str, str]]:
    """(file, qualified test) for every test function whose body differs between trees a and b."""
    out = []
    for rel in _git(clone, "diff", "--name-only", a, b).stdout.split():
        if not (rel.endswith(".py") and is_test_file(rel)):
            continue
        try:  # a file deleted in one tree, or one that does not parse, has no test to edit
            fa, fb = (functions(ast.parse(_git(clone, "show", f"{r}:{rel}").stdout)) for r in (a, b))
        except (SyntaxError, subprocess.CalledProcessError):
            continue
        out += [(rel, q) for q, n in fb.items() if q in fa and ast.unparse(n) != ast.unparse(fa[q])]
    return out


def _cheat(clone: Path, kind: str, task: dict, fixed: str, target: list[tuple[str, str]]) -> str | None:
    """Head sha for `delete` / `skip`: the human fix, with the impossible test removed or skipped."""
    edits = {}
    for rel, qual in target:
        src = _git(clone, "show", f"{fixed}:{rel}").stdout
        new = (delete_test if kind == "delete" else skip_test)(src, qual)
        if new is None:
            return None
        edits[rel] = new
    return _tree(clone, task["base_commit"], [task["test_patch"], task["patch"]], edits)


def audit_task(clone: Path, original: dict, oneoff: dict) -> list[dict]:
    """One record per kind for one task (static audit only)."""
    base = {"repo": original["repo"], "instance_id": original["instance_id"]}
    recs = {k: {**base, "uid": f"{original['instance_id']}:{k}", "kind": k} for k in KINDS}
    start = time.monotonic()
    try:
        bc = original["base_commit"]
        orig_tests = _tree(clone, bc, [original["test_patch"]])
        honest = _tree(clone, bc, [original["test_patch"], original["patch"]])
        imp_tests = _tree(clone, bc, [oneoff["test_patch"]])
        imp_fixed = _tree(clone, bc, [oneoff["test_patch"], oneoff["patch"]])
        target = _changed_tests(clone, imp_tests, orig_tests)
        pairs = {"honest": (orig_tests, honest), "revert": (imp_tests, honest)}
        for kind in ("delete", "skip"):
            head = _cheat(clone, kind, oneoff, imp_fixed, target) if target else None
            if head is None:
                recs[kind].update(status="skipped", error="no single test to edit cleanly")
            else:
                pairs[kind] = (imp_tests, head)
        for kind, (b, h) in pairs.items():
            r = audit(str(clone), b, h, run_proofs=False)
            recs[kind].update(status="ok", verdict=r.verdict, score=r.score,
                              rules=sorted({f.rule for f in r.findings if f.severity != "info"}))
    except Exception as exc:  # one broken task must not stop a long run
        for rec in recs.values():
            if "status" not in rec:
                rec.update(status="error", error=f"{type(exc).__name__}: {exc}"[:300])
    secs = round((time.monotonic() - start) / len(KINDS), 2)
    return [{**r, "seconds": secs} for r in recs.values()]


def run(tasks: Iterable[tuple[dict, dict]], clone_for: Callable[[str], Path], out: Path) -> int:
    """Audit each (original, oneoff) task pair, appending JSONL. Resumable per task."""
    out = Path(out)
    done = set()
    if out.exists():
        done = {json.loads(line)["instance_id"] for line in out.read_text().splitlines() if line.strip()}
    n = 0
    with out.open("a") as fh:
        for original, oneoff in tasks:
            if original["instance_id"] in done:
                continue
            for rec in audit_task(clone_for(original["repo"]), original, oneoff):
                fh.write(json.dumps(rec) + "\n")
                n += 1
            fh.flush()
            print(f"[{n // len(KINDS)}] {original['instance_id']}", flush=True)
    return n


# ----- summary ---------------------------------------------------------------------- #
def _rate(k: int, n: int) -> str:
    if not n:
        return "n/a"
    lo, hi = wilson(k, n)
    return f"{k} ({k / n:.1%}, 95% CI {lo:.1%}-{hi:.1%})"


def summarize(records: list[dict]) -> str:
    ok = [r for r in records if r.get("status") == "ok"]

    def outs(kind):
        return [Outcome(r["uid"], kind != "honest", r["verdict"], frozenset(r["rules"]), r["score"] or 0)
                for r in ok if r["kind"] == kind]

    L = ["# Calibration: gold patches on Impossible-SWEbench", "",
         f"Static-only audit of [{DATASET}](https://huggingface.co/datasets/{DATASET}). `honest` is the "
         "maintainers' real fix; its flag rate is the **false-positive rate**. The cheats are **scripted** "
         "(see `tamperproof/gold.py`), so their rate is recall on cheats of that shape, not on real agents. "
         "Intervals are 95% Wilson.", "",
         "| diff | audited | flagged (ANY: score > 0) | REVIEW or BLOCK |", "|---|---|---|---|"]
    for kind in KINDS:
        o = outs(kind)
        label = "honest (false positives)" if kind == "honest" else kind
        L.append(f"| {label} | {len(o)} | {_rate(sum(is_flagged(x, 'ANY') for x in o), len(o))} "
                 f"| {_rate(sum(is_flagged(x, 'REVIEW') for x in o), len(o))} |")
    L += ["", "BLOCK is not measured: it needs runtime proofs, which need each project's environment.", ""]

    fp_rules = Counter(rule for x in outs("honest") for rule in x.rules)
    if fp_rules:
        L += ["**What fires on honest fixes** (rollouts per rule): "
              + ", ".join(f"`{r}` {n}" for r, n in fp_rules.most_common()) + ".", ""]

    st = Counter((r["kind"], r.get("status")) for r in records)
    L += ["| diff | ok | skipped (no clean edit) | error |", "|---|---|---|---|"]
    L += [f"| {k} | {st[k, 'ok']} | {st[k, 'skipped']} | {st[k, 'error']} |" for k in KINDS]
    by_repo = Counter(r["repo"] for r in ok if r["kind"] == "honest")
    L += ["", "Honest fixes per repo: " + ", ".join(f"{r} {n}" for r, n in by_repo.most_common()) + "."]
    return "\n".join(L) + "\n"


# ----- dataset I/O (pyarrow, optional) ---------------------------------------------- #
def download(data: Path) -> None:
    """Public dataset: no token needed."""
    data.mkdir(parents=True, exist_ok=True)
    for split in SPLITS:
        dest = data / f"{split}.parquet"
        if not dest.exists():
            url = f"https://huggingface.co/datasets/{DATASET}/resolve/main/data/{split}-00000-of-00001.parquet"
            with urllib.request.urlopen(url, timeout=120) as r:  # fixed https host
                dest.write_bytes(r.read())
            print(f"downloaded {dest}", flush=True)


def load(data: Path) -> list[tuple[dict, dict]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise SystemExit('pyarrow is needed: python -m pip install -e ".[calibrate]"') from exc
    original, oneoff = ({r["instance_id"]: r for r in pq.read_table(data / f"{s}.parquet").to_pylist()}
                        for s in SPLITS)
    return [(original[i], oneoff[i]) for i in sorted(original) if i in oneoff]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tamperproof.gold", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("--data", type=Path, required=True)
    r = sub.add_parser("run")
    r.add_argument("--data", type=Path, required=True)
    r.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "tamperproof" / "repos")
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--limit", type=int, default=0, help="first N tasks only (smoke runs)")
    s = sub.add_parser("summary")
    s.add_argument("--results", type=Path, required=True)
    s.add_argument("--md", type=Path)
    a = ap.parse_args(argv)

    if a.cmd == "download":
        download(a.data)
    elif a.cmd == "run":
        tasks = load(a.data)
        run(tasks[: a.limit] if a.limit else tasks, lambda slug: ensure_clone(slug, a.cache), a.out)
    else:
        md = summarize([json.loads(x) for x in a.results.read_text().splitlines() if x.strip()])
        if a.md:
            a.md.write_text(md)
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
