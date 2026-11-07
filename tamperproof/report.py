"""Terminal, Markdown and SARIF 2.1.0 output (SARIF is what Qodana and GitHub code scanning ingest)."""

from __future__ import annotations

import json

from .audit import AuditResult
from .static_rules import RULES

LEVEL = {"high": "error", "medium": "warning", "low": "note", "info": "note"}


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
        md.append(f"| {_tag(f)} | {f.rule} {f.title} | `{f.file}:{f.line}` | {f.message} |")
    proven = [f for f in r.findings if f.proven]
    if proven:
        md += ["", "### Runtime proof"]
        md += [f"- **{f.rule}** `{f.file}:{f.line}`: {f.proof}" for f in proven]
    return "\n".join(md)


def to_sarif(r: AuditResult) -> str:
    used = sorted({f.rule for f in r.findings})
    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "tamperproof",
                "informationUri": "https://github.com/VaughnAntonio/tamperproof",
                "rules": [{"id": rid, "shortDescription": {"text": RULES[rid]}} for rid in used],
            }},
            "results": [{
                "ruleId": f.rule,
                "level": "error" if f.proven else LEVEL[f.severity],
                "message": {"text": f"{f.message}" + (f" | PROOF: {f.proof}" if f.proof else "")},
                "locations": [{"physicalLocation": {
                    "artifactLocation": {"uri": f.file},
                    "region": {"startLine": max(1, f.line)},  # GitHub rejects 0
                }}],
                "properties": {"proven": f.proven, "severity": f.severity},
            } for f in r.findings],
            "properties": {"verdict": r.verdict, "score": r.score},
        }],
    }
    return json.dumps(sarif, indent=2)
