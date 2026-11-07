"""Test runners for the proof engine: pytest (Python) and Gradle (Kotlin/JUnit, roadmap 7).

The experiments in `proof.py` don't care which language ran the tests. They only need
`{test_id: passed|failed|skipped|error}`. Both pytest and Gradle write JUnit XML, so one
parser serves both. Ids are `<classname>.<name>`: `test_m.test_a` for pytest,
`PricingTest.tierBoundary50` for Gradle (the `()` Gradle appends is stripped, so a
Kotlin `fun` name matches the id exactly).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

_CALL_PARENS = re.compile(r"\(.*\)$")


def parse_junit_xml(paths: Iterable[Path], strip_call_parens: bool = False) -> dict[str, str]:
    """Outcome per test id across JUnit XML files."""
    results: dict[str, str] = {}
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            name = case.get("name") or ""
            if strip_call_parens:
                name = _CALL_PARENS.sub("", name)
            tid = f"{case.get('classname')}.{name}"
            if case.find("failure") is not None:
                results[tid] = "failed"
            elif case.find("error") is not None:
                results[tid] = "error"
            elif case.find("skipped") is not None:
                results[tid] = "skipped"
            else:
                results[tid] = "passed"
    return results


class TestRunner(Protocol):
    def run(self, root: Path, timeout: int = 300) -> dict[str, str]: ...


class PytestRunner:
    """Unchanged behaviour of the original `proof.run_pytest`."""

    def run(self, root: Path, timeout: int = 300) -> dict[str, str]:
        xml = root / ".tamperproof-junit.xml"
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             f"--junitxml={xml}", "-o", "junit_family=xunit2"],
            cwd=root, capture_output=True, text=True, timeout=timeout,
        )
        return parse_junit_xml([xml]) if xml.exists() else {}


class GradleRunner:
    """`gradle test` in the exported tree, then read build/test-results/test/*.xml.

    `--continue` keeps going after failures (a failing test must still produce XML);
    the exit code is ignored for the same reason. A compile error yields no XML, so the
    result is empty, which the audit reports as a red suite, never as a proof.
    """

    def run(self, root: Path, timeout: int = 900) -> dict[str, str]:
        gradle = shutil.which("gradle")
        if gradle is None:
            # No results, not a crash: a crash exits 1 (reads as REVIEW), while an empty
            # run leaves every finding unproven, which can never BLOCK.
            print("tamperproof: gradle not found on PATH; Kotlin tests not run", file=sys.stderr)
            return {}
        subprocess.run(
            [gradle, "test", "--continue", "--console=plain", "--quiet"],
            cwd=root, capture_output=True, text=True, timeout=timeout,
        )
        return self.collect(root)

    @staticmethod
    def collect(root: Path) -> dict[str, str]:
        files = sorted((root / "build" / "test-results" / "test").glob("*.xml"))
        return parse_junit_xml(files, strip_call_parens=True)


GRADLE_BUILD_FILES = ("build.gradle.kts", "build.gradle")


def runner_for(root: Path) -> TestRunner:
    """Gradle when the project root has a Gradle build file, else pytest."""
    if any((root / name).exists() for name in GRADLE_BUILD_FILES):
        return GradleRunner()
    return PytestRunner()
