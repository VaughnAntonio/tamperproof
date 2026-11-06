"""Calibrate tamperproof on AlignmentResearch/impossible-swe-nemotron-rollouts.

    python -m tamperproof.calibrate_run download --data data/
    python -m tamperproof.calibrate_run run      --data data/ --out results.jsonl
    python -m tamperproof.calibrate_run summary  --data data/ --results results.jsonl --md docs/CALIBRATION.md

Static-only: the proof stage needs each SWE-Gym sandbox image, which the dataset does not
ship, so BLOCK precision is reported as "not measured" rather than guessed.
Needs the optional extra: `python -m pip install -e ".[calibrate]"` (pyarrow).
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path

from .audit import audit
from .calibration import Outcome, confusion, per_rule, precision_at_prevalence, sample, stratum, wilson
from .rebuild import build_pair, ensure_clone

DATASET = "AlignmentResearch/impossible-swe-nemotron-rollouts"
ROLLOUTS = "rollouts_truncated"  # tree at the first passing submit = what the grader rewarded
INDEX_COLUMNS = ["rollout_uid", "task_key", "instance_id", "mutation_type", "rl_step",
                 "label_class", "is_hack", "is_cleanly_honest"]
DEFAULT_QUOTA = "hack=800,honest=800,attempt=200,other_test=200"


# ----- one rollout --------------------------------------------------------------- #
def audit_rollout(row: dict, task: dict, messages: list[dict], clone: Path) -> dict:
    rec = {k: row.get(k) for k in ("rollout_uid", "task_key", "instance_id", "label_class")}
    rec.update(stratum=stratum(row), proofs=False, verdict=None, score=None, rules=[], partial=[], error=None)
    start = time.monotonic()
    try:
        pair = build_pair(clone, task, messages)
        rec["partial"] = pair.replay.partial
        if pair.replay.partial:
            rec["status"] = "partial"  # tree may be inexact: excluded, never guessed
        else:
            result = audit(str(clone), pair.base, pair.head, run_proofs=False)
            rec.update(status="ok", verdict=result.verdict, score=result.score,
                       rules=sorted({f.rule for f in result.findings if f.severity != "info"}))
    except Exception as exc:  # one bad rollout (RebuildError, git failure, ...) must not stop a long run
        rec.update(status="error", error=f"{type(exc).__name__}: {exc}"[:300])
    rec["seconds"] = round(time.monotonic() - start, 2)
    return rec


def run(rows: Iterable[dict], tasks: dict[str, dict], messages_for: Callable[[str], list[dict] | None],
        clone_for: Callable[[str], Path], out: Path) -> int:
    """Audit each row once, appending to `out` (JSONL). Returns how many were new.

    Resumable: rollouts already in `out` are skipped, so a crash costs one rollout.
    """
    out = Path(out)
    done = set()
    if out.exists():
        done = {json.loads(line)["rollout_uid"] for line in out.read_text().splitlines() if line.strip()}
    n = 0
    with out.open("a") as fh:
        for row in rows:
            uid = row["rollout_uid"]
            if uid in done:
                continue
            task, msgs = tasks.get(row["task_key"]), messages_for(uid)
            if task is None or msgs is None:
                rec = {**{k: row.get(k) for k in ("rollout_uid", "task_key", "label_class")},
                       "stratum": stratum(row), "status": "error", "error": "task or messages missing"}
            else:
                rec = audit_rollout(row, task, msgs, clone_for(task["repo"]))
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            n += 1
            print(f"[{n}] {uid} {rec['status']} {rec.get('verdict') or ''} {rec.get('seconds', '')}s",
                  flush=True)
    return n


# ----- summary --------------------------------------------------------------------- #
def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _ci(k: int, n: int) -> str:
    w = wilson(k, n)
    return "" if w is None else f" [{_pct(w[0])}, {_pct(w[1])}]"


def summarize(records: list[dict], population: dict[str, int]) -> str:
    ok = [r for r in records if r.get("status") == "ok"]
    outs = {s: [Outcome(r["rollout_uid"], s in ("hack", "attempt"), r["verdict"],
                        frozenset(r.get("rules") or ()), r.get("score") or 0)
                for r in ok if r.get("stratum") == s]
            for s in ("hack", "honest", "attempt", "other_test")}
    hack, honest = outs["hack"], outs["honest"]
    proofs = any(r.get("proofs") for r in ok)
    L = ["# Calibration: impossible-swe-nemotron-rollouts", "",
         f"Static-only run over `{ROLLOUTS}`. Positives are `is_hack`, negatives `is_cleanly_honest`. "
         f"Precision is rebuilt at the population ratio ({population.get('hack', 0):,} hacks : "
         f"{population.get('honest', 0):,} honest) from per-stratum recall and false-positive rate, "
         "because the sample is stratified. Brackets are 95% Wilson intervals.", "",
         "| threshold | recall (hacks flagged) | false-positive rate (honest flagged) "
         "| precision at population |",
         "|---|---|---|---|"]
    for th in ("ANY", "REVIEW", "BLOCK"):
        if th == "BLOCK" and not proofs:
            L.append("| BLOCK | not measured | not measured | not measured: static-only run "
                     "(proofs need the SWE-Gym sandbox images) |")
            continue
        tp = confusion(hack, th).tp
        fp = confusion(honest, th).fp
        p, ci = precision_at_prevalence(tp, len(hack), fp, len(honest),
                                        population.get("hack", 0), population.get("honest", 0))
        pci = "" if ci is None else f" [{_pct(ci[0])}, {_pct(ci[1])}]"
        L.append(f"| {th} | {_pct(tp / len(hack) if hack else None)}{_ci(tp, len(hack))} "
                 f"| {_pct(fp / len(honest) if honest else None)}{_ci(fp, len(honest))} | {_pct(p)}{pci} |")
    L += ["", "`ANY` = at least one scored finding, even below the REVIEW threshold (score 15).", ""]

    L += ["## Sample and exclusions", "", "| stratum | sampled | audited | partial (excluded) | error |",
          "|---|---|---|---|---|"]
    for s in ("hack", "honest", "attempt", "other_test"):
        rs = [r for r in records if r.get("stratum") == s]
        c = Counter(r.get("status") for r in rs)
        L.append(f"| {s} | {len(rs)} | {c['ok']} | {c['partial']} | {c['error']} |")
    reasons = Counter(p.split(":")[0] + ":" + p.split(":")[1] if p.count(":") >= 1 else p
                      for r in records for p in (r.get("partial") or [])[:1])
    if reasons:
        L += ["", "Partial means the transcript wrote a `.py` file in a way replay does not reproduce "
                  "exactly, so the rollout is left out rather than guessed. Most common first reasons: "
                  + ", ".join(f"`{k}` ({v})" for k, v in reasons.most_common(6)) + "."]

    L += ["", "## Flag rate by label class", "",
          "| label_class | audited | ANY | REVIEW |", "|---|---|---|---|"]
    by_class: dict[str, list[dict]] = {}
    for r in ok:
        by_class.setdefault(r.get("label_class") or "?", []).append(r)
    for cls in sorted(by_class):
        rs = by_class[cls]
        anyc = sum((r.get("score") or 0) > 0 or r["verdict"] != "PASS" for r in rs)
        rev = sum(r["verdict"] in ("REVIEW", "BLOCK") for r in rs)
        L.append(f"| {cls} | {len(rs)} | {_pct(anyc / len(rs))} | {_pct(rev / len(rs))} |")
    L += ["", "`attempt` rows (failed tampering left in the tree) are tampering the tool *should* flag; "
              "`nonhack_other_test_written` rows (agent wrote only its own tests) are a precision "
              "stress test.",
          ""]

    L += ["## Per rule (hack vs honest, audited rows)", "",
          "| rule | rollouts fired | precision in sample | recall |", "|---|---|---|---|"]
    for s in per_rule(hack + honest):
        L.append(f"| {s.rule} | {s.fired} | {_pct(s.precision)} | {_pct(s.recall)} |")
    L += ["", "Per-rule precision is on the stratified sample, not corrected to the population.", ""]

    secs = [r["seconds"] for r in records if isinstance(r.get("seconds"), (int, float))]
    if secs:
        L.append(f"Median time per rollout: {statistics.median(secs):.1f}s over {len(secs)} rollouts.")
    return "\n".join(L) + "\n"


# ----- dataset I/O (pyarrow, optional) -------------------------------------------------- #
def _pa():
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise SystemExit('pyarrow is needed: python -m pip install -e ".[calibrate]"') from exc
    return pq


def _token() -> str:
    tok = os.environ.get("HF_TOKEN") or ""
    f = Path.home() / ".cache" / "huggingface" / "token"
    if not tok and f.exists():
        tok = f.read_text().strip()
    if not tok:
        raise SystemExit("No Hugging Face token: put a read token in ~/.cache/huggingface/token")
    return tok


def _get(url: str, token: str) -> urllib.request.addinfourl:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    return urllib.request.urlopen(req, timeout=120)  # fixed https host, never user-supplied


def download(data: Path, dirs: Iterable[str] = ("tasks", ROLLOUTS, "reviews")) -> None:
    token = _token()
    for d in dirs:
        with _get(f"https://huggingface.co/api/datasets/{DATASET}/tree/main/{d}", token) as resp:
            listing = json.load(resp)
        for item in listing:
            if item.get("type") != "file":
                continue
            dest = data / item["path"]
            if dest.exists() and dest.stat().st_size == item.get("size"):
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".part")
            with _get(f"https://huggingface.co/datasets/{DATASET}/resolve/main/{item['path']}", token) as r, \
                    tmp.open("wb") as fh:
                while chunk := r.read(1 << 20):
                    fh.write(chunk)
            tmp.replace(dest)
            print(f"downloaded {item['path']} ({item.get('size', 0) // 1024} KiB)", flush=True)


def load_index(data: Path) -> list[dict]:
    pq = _pa()
    rows = []
    for f in sorted((data / ROLLOUTS).glob("*.parquet")):
        for r in pq.read_table(f, columns=INDEX_COLUMNS).to_pylist():
            r["_file"] = str(f)
            rows.append(r)
    return rows


def load_tasks(data: Path) -> dict[str, dict]:
    pq = _pa()
    cols = ["task_key", "repo", "base_commit", "test_patch", "graded_test_files"]
    return {t["task_key"]: t for f in sorted((data / "tasks").glob("*.parquet"))
            for t in pq.read_table(f, columns=cols).to_pylist()}


def load_messages(rows: Iterable[dict]) -> dict[str, list[dict]]:
    pq = _pa()
    want: dict[str, set[str]] = {}
    for r in rows:
        want.setdefault(r["_file"], set()).add(r["rollout_uid"])
    out = {}
    for f, uids in want.items():
        for r in pq.read_table(f, columns=["rollout_uid", "messages"]).to_pylist():
            if r["rollout_uid"] in uids:
                out[r["rollout_uid"]] = r["messages"]
    return out


def population(index: Iterable[dict]) -> dict[str, int]:
    return dict(Counter(s for s in map(stratum, index) if s))


def _quota(text: str) -> dict[str, int]:
    return {k: int(v) for k, v in (p.split("=") for p in text.split(",") if p)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tamperproof.calibrate_run", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("--data", type=Path, required=True)
    r = sub.add_parser("run")
    r.add_argument("--data", type=Path, required=True)
    r.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "tamperproof" / "repos")
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--quota", default=DEFAULT_QUOTA)
    r.add_argument("--per-task", type=int, default=3)
    r.add_argument("--limit", type=int, default=0, help="stop after N sampled rollouts (smoke runs)")
    s = sub.add_parser("summary")
    s.add_argument("--data", type=Path, required=True)
    s.add_argument("--results", type=Path, required=True)
    s.add_argument("--md", type=Path)
    a = ap.parse_args(argv)

    if a.cmd == "download":
        download(a.data)
        return 0
    if a.cmd == "run":
        index = load_index(a.data)
        rows = sample(index, _quota(a.quota), seed=a.seed, per_task=a.per_task)
        if a.limit:
            rows = rows[: a.limit]
        print(f"sampled {len(rows)} of {len(index)} rollouts", flush=True)
        tasks = load_tasks(a.data)
        msgs = load_messages(rows)
        run(rows, tasks, msgs.get, lambda slug: ensure_clone(slug, a.cache), a.out)
        return 0
    records = [json.loads(line) for line in a.results.read_text().splitlines() if line.strip()]
    md = summarize(records, population(load_index(a.data)))
    if a.md:
        a.md.write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
