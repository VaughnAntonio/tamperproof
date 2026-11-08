"""Terminal, Markdown and SARIF 2.1.0 output (SARIF is what Qodana and GitHub code scanning ingest)."""

from __future__ import annotations

import hashlib
import json
import re

from .audit import AuditResult
from .static_rules import RULES

# Suspicion caps at "warning": GitHub fails the code-scanning check on any "error",
# so only a proven finding may produce one (same rule as "only proof can BLOCK").
LEVEL = {"high": "warning", "medium": "warning", "low": "note", "info": "note"}


def _sarif_level(f) -> str:
    if f.proven:
        return "error"
    if f.proven is False:
        return "note"  # disproven: kept visible as cleared, never alarming
    return LEVEL[f.severity]


def _fingerprint(f) -> str:
    """Stable alert identity for code scanning (Qodana uses `equalIndicator/v1` the same way).

    Built from rule + file + the first `code` span of the message (test name, function,
    branch), not the line number, so an alert survives code moving and a BR01 call-site
    count change. Falls back to the whole message when it has no code span.
    """
    m = re.search(r"`([^`]+)`", f.message)
    key = f"{f.rule}|{f.file}|{m.group(1) if m else f.message}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def _tag(f) -> str:
    return "PROVEN" if f.proven else "disproven" if f.proven is False else "suspected"


def to_text(r: AuditResult) -> str:
    green = "GREEN" if r.agent_tests_green else "RED"
    lines = [
        f"tamperproof  {r.base}..{r.head}",
        f"Agent's own test suite: {green} ({len(r.agent_tests)} tests)",
        f"Verdict: {r.verdict}   suspicion score {r.score}/100",
        "",
    ]
    for f in r.findings:
        if f.severity == "info":
            continue
        lines.append(f"[{_tag(f):>9}] {f.rule} {f.severity:<6} {f.file}:{f.line}  {f.title}: {f.message}")
        if f.proof:
            lines.append(f"{'':12}proof: {f.proof}")
    info = [f for f in r.findings if f.severity == "info"]
    if info:
        lines.append("")
        lines += [f"  info  {f.file}:{f.line}  {f.message}" for f in info]
    return "\n".join(lines)


def to_markdown(r: AuditResult) -> str:
    icon = {"BLOCK": "⛔", "REVIEW": "⚠️", "PASS": "✅"}[r.verdict]
    md = [
        f"## {icon} tamperproof: {r.verdict}",
        f"Suspicion score **{r.score}/100**. Agent's own tests: "
        f"**{'green' if r.agent_tests_green else 'red'}** ({len(r.agent_tests)} tests).",
        "",
        "| Status | Rule | Where | Finding |",
        "|---|---|---|---|",
    ]
    for f in r.findings:
        if f.severity == "info":
            continue
        cell = f.message.replace("|", "\\|")  # agent code like `a | b` would split the row
        md.append(f"| {_tag(f)} | {f.rule} {f.title} | `{f.file}:{f.line}` | {cell} |")
    proven = [f for f in r.findings if f.proven]
    if proven:
        md += ["", "### Runtime proof"]
        md += [f"- **{f.rule}** `{f.file}:{f.line}`: {f.proof}" for f in proven]
    return "\n".join(md)


def to_sarif(r: AuditResult) -> str:
    from . import __version__  # lazy: the package __init__ defines it after importing audit

    used = sorted({f.rule for f in r.findings})
    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "tamperproof",
                "version": __version__,
                "informationUri": "https://github.com/VaughnAntonio/tamperproof",
                "rules": [{"id": rid, "shortDescription": {"text": RULES[rid]}} for rid in used],
            }},
            "results": [{
                "ruleId": f.rule,
                "level": _sarif_level(f),
                "message": {"text": f"{f.message}" + (f" | PROOF: {f.proof}" if f.proof else "")},
                "locations": [{"physicalLocation": {
                    "artifactLocation": {"uri": f.file},
                    "region": {"startLine": max(1, f.line)},  # GitHub rejects 0
                }}],
                "partialFingerprints": {"tamperproof/v1": _fingerprint(f)},
                "properties": {"proven": f.proven, "severity": f.severity},
            } for f in r.findings],
            # Same value as the action's `category: tamperproof`; GitHub appends the "/".
            "automationDetails": {"id": "tamperproof/"},
            "properties": {"verdict": r.verdict, "score": r.score},
        }],
    }
    return json.dumps(sarif, indent=2)
