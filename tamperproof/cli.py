"""Command line entry point.

    python -m tamperproof <repo> --base main --head agent/fix-123 [--sarif out.sarif] [--md out.md]

Exit codes: 0 PASS, 1 REVIEW, 2 BLOCK (so CI can gate on it), 3 the audit itself crashed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .audit import audit
from .report import to_markdown, to_sarif, to_text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tamperproof",
                                 description="Prove whether an AI agent's diff games the test suite.")
    ap.add_argument("repo", help="Path to a git repository")
    ap.add_argument("--base", required=True, help="Ref before the agent's change")
    ap.add_argument("--head", default="HEAD", help="Ref with the agent's change")
    ap.add_argument("--no-proof", action="store_true", help="Static analysis only (no test runs)")
    ap.add_argument("--sarif", help="Write SARIF 2.1.0 report (Qodana / GitHub code scanning)")
    ap.add_argument("--md", help="Write a Markdown summary (for a PR comment)")
    args = ap.parse_args(argv)

    try:
        result = audit(args.repo, args.base, args.head, run_proofs=not args.no_proof)
    except Exception as exc:  # any crash, including a test run that hangs past its timeout
        # Python's own crash code is 1, which reads as REVIEW and passes a `fail-on: block` gate.
        # An agent could cause that on purpose (a test that never returns), so a crash is its own code.
        print(f"tamperproof crashed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    print(to_text(result))
    if args.sarif:
        Path(args.sarif).write_text(to_sarif(result))
    if args.md:
        Path(args.md).write_text(to_markdown(result))
    return {"PASS": 0, "REVIEW": 1, "BLOCK": 2}[result.verdict]


if __name__ == "__main__":
    sys.exit(main())
