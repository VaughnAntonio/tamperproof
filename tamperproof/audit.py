"""Orchestrates static analysis + proof engine and produces a verdict."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import gitio, proof
from .kotlin_sketch import diff_kotlin_tests, is_kotlin_test_file
from .runners import runner_for
from .static_rules import (
    SEVERITY_WEIGHT,
    Finding,
    changed_functions,
    coverage_and_blast,
    diff_source,
    diff_tests,
    is_test_file,
    test_literals,
)


@dataclass
class AuditResult:
    base: str
    head: str
    findings: list[Finding]
    agent_tests: dict[str, str] = field(default_factory=dict)

    @property
    def score(self) -> int:
        """0-100 suspicion score. Proven findings count double, disproven ones count zero."""
        total = 0.0
        for f in self.findings:
            w = SEVERITY_WEIGHT[f.severity]
            total += w * (2 if f.proven else 0 if f.proven is False else 1)
        return min(100, round(total))

    @property
    def verdict(self) -> str:
        # Only runtime proof can BLOCK. Static suspicion alone can at most ask for review.
        if any(f.proven for f in self.findings):
            return "BLOCK"
        if self.score >= 15:
            return "REVIEW"
        return "PASS"

    @property
    def agent_tests_green(self) -> bool:
        return bool(self.agent_tests) and all(v in ("passed", "skipped") for v in self.agent_tests.values())


def audit(repo: str, base: str, head: str = "HEAD", run_proofs: bool = True) -> AuditResult:
    changes = gitio.changed_py_files(repo, base, head)
    head_sources = gitio.all_py_files(repo, head)
    head_tests = {p: s for p, s in head_sources.items() if is_test_file(p)}
    lits = test_literals(head_tests)
    test_text = "\n".join(head_tests.values())

    findings: list[Finding] = []
    for _status, old, new in changes:
        path = new or old
        before = gitio.read_at(repo, base, old)
        after = gitio.read_at(repo, head, new)
        if is_test_file(path):
            findings += diff_tests(path, before, after)
        else:
            findings += diff_source(path, before, after, lits)
            findings += coverage_and_blast(path, changed_functions(before, after), test_text, head_sources)

    # Kotlin/JUnit sketch (roadmap 7): stand-in detector for test files only; see kotlin_sketch.py.
    for _status, old, new in gitio.changed_files(repo, base, head, (".kt",)):
        path = new or old
        if is_kotlin_test_file(path):
            before, after = gitio.read_at(repo, base, old), gitio.read_at(repo, head, new)
            findings += diff_kotlin_tests(path, before, after)

    agent_tests: dict[str, str] = {}
    if run_proofs and any(f.rule.startswith(("TT", "SC01", "SC02")) for f in findings):
        agent_tests = proof.prove(repo, base, head, findings, head_tests)
    elif run_proofs:
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            tree = gitio.export_tree(repo, head, Path(d))
            agent_tests = runner_for(tree).run(tree)

    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    findings.sort(key=lambda f: (not f.proven, order[f.severity], f.file, f.line))
    return AuditResult(base, head, findings, agent_tests)
