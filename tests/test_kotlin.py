"""Kotlin/JUnit sketch (roadmap 7): Gradle runner, stand-in detector, end-to-end proof.

Everything except the last section runs without a JDK or Gradle. The end-to-end
tests need `gradle` on PATH and are skipped (visibly) without it; CI runs them.
"""

import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo"))

from tamperproof.kotlin_sketch import diff_kotlin_tests, is_kotlin_test_file  # noqa: E402
from tamperproof.proof import _match  # noqa: E402
from tamperproof.runners import GradleRunner, PytestRunner, parse_junit_xml, runner_for  # noqa: E402

# Shape of build/test-results/test/TEST-PricingTest.xml as written by Gradle's JUnit Platform runner.
GRADLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="PricingTest" tests="3" skipped="1" failures="1" errors="0"
           timestamp="2026-11-07T12:00:00" hostname="runner" time="0.03">
  <properties/>
  <testcase name="noDiscount()" classname="PricingTest" time="0.001"/>
  <testcase name="tierBoundary50()" classname="PricingTest" time="0.0">
    <skipped/>
  </testcase>
  <testcase name="checkoutAtBoundary()" classname="PricingTest" time="0.01">
    <failure message="org.opentest4j.AssertionFailedError: expected: &lt;482.99&gt; but was: &lt;507.99&gt;"
             type="org.opentest4j.AssertionFailedError">stack</failure>
  </testcase>
  <system-out><![CDATA[]]></system-out>
  <system-err><![CDATA[]]></system-err>
</testsuite>
"""


# ----- runners ------------------------------------------------------------------ #
def test_gradle_xml_parses_with_bare_method_names(tmp_path):
    xml = tmp_path / "TEST-PricingTest.xml"
    xml.write_text(GRADLE_XML)
    assert parse_junit_xml([xml], strip_call_parens=True) == {
        "PricingTest.noDiscount": "passed",
        "PricingTest.tierBoundary50": "skipped",
        "PricingTest.checkoutAtBoundary": "failed",
    }


def test_gradle_runner_reads_every_result_file(tmp_path):
    results = tmp_path / "build" / "test-results" / "test"
    results.mkdir(parents=True)
    (results / "TEST-PricingTest.xml").write_text(GRADLE_XML)
    (results / "TEST-Other.xml").write_text(
        GRADLE_XML.replace('classname="PricingTest"', 'classname="com.acme.Other"'))
    out = GradleRunner().collect(tmp_path)
    assert out["PricingTest.noDiscount"] == "passed"
    assert out["com.acme.Other.checkoutAtBoundary"] == "failed"


def test_runner_chosen_by_build_file(tmp_path):
    assert isinstance(runner_for(tmp_path), PytestRunner)
    (tmp_path / "build.gradle.kts").write_text("")
    assert isinstance(runner_for(tmp_path), GradleRunner)


def test_match_kotlin_file_against_gradle_ids():
    results = {"PricingTest.tierBoundary50": "skipped", "PricingTest.tierBoundary5": "passed"}
    assert _match(results, "tierBoundary50", "src/test/kotlin/PricingTest.kt") == {
        "PricingTest.tierBoundary50": "skipped"}


def test_match_kotlin_package_path():
    results = {"com.acme.PricingTest.a": "passed", "other.PricingTest.a": "failed"}
    assert _match(results, "a", "src/test/kotlin/com/acme/PricingTest.kt") == {
        "com.acme.PricingTest.a": "passed"}


# ----- stand-in detector ------------------------------------------------------------ #
BASE_KT = """\
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test

class PricingTest {
    @Test
    fun tierBoundary50() {
        assertEquals(475.0, bulkPrice(50, 10.0), 0.001)
    }

    @Test
    fun largeOrder() {
        assertEquals(1080.0, bulkPrice(120, 10.0), 0.001)
    }

    @Test
    fun checkoutAtBoundary() {
        assertEquals(482.99, checkout(50, 10.0, 2.0), 0.001)
    }

    @Test
    fun shipping() {
        assertEquals(7.99, shippingCost(2.0), 0.01)
    }

    private fun helper() = "{not a test}"
}
"""


def _rules(after):
    return sorted((f.rule, f.data["test"]) for f in diff_kotlin_tests("src/test/kotlin/PricingTest.kt",
                                                                       BASE_KT, after))


def test_unchanged_file_has_no_findings():
    assert _rules(BASE_KT) == []


def test_new_test_is_not_a_finding():
    after = BASE_KT.replace("    private fun", "    @Test\n    fun extra() {\n        assertEquals(1, 1)\n"
                                              "    }\n\n    private fun")
    assert _rules(after) == []


def test_deleted_test_is_tt01():
    after = BASE_KT.replace("""    @Test
    fun largeOrder() {
        assertEquals(1080.0, bulkPrice(120, 10.0), 0.001)
    }

""", "")
    assert _rules(after) == [("TT01", "largeOrder")]


def test_disabled_test_is_tt03():
    after = BASE_KT.replace("    @Test\n    fun tierBoundary50", "    @Disabled(\"flaky\")\n    @Test\n"
                                                                "    fun tierBoundary50")
    assert _rules(after) == [("TT03", "tierBoundary50")]


def test_junit4_ignore_is_tt03():
    after = BASE_KT.replace("    @Test\n    fun largeOrder", "    @Ignore\n    @Test\n    fun largeOrder")
    assert _rules(after) == [("TT03", "largeOrder")]


def test_weakened_assertion_is_tt05():
    after = BASE_KT.replace("assertEquals(482.99, checkout(50, 10.0, 2.0), 0.001)",
                            "assertTrue(checkout(50, 10.0, 2.0) > 0)")
    findings = diff_kotlin_tests("src/test/kotlin/PricingTest.kt", BASE_KT, after)
    assert [(f.rule, f.data["test"]) for f in findings] == [("TT05", "checkoutAtBoundary")]
    assert "482.99" in findings[0].message and "assertTrue" in findings[0].message


def test_removed_assertion_is_tt02():
    after = BASE_KT.replace("        assertEquals(7.99, shippingCost(2.0), 0.01)\n", "")
    assert _rules(after) == [("TT02", "shipping")]


def test_loosened_delta_is_tt06_not_tt05():
    after = BASE_KT.replace("shippingCost(2.0), 0.01)", "shippingCost(2.0), 1.0)")
    findings = diff_kotlin_tests("src/test/kotlin/PricingTest.kt", BASE_KT, after)
    assert [(f.rule, f.data["test"]) for f in findings] == [("TT06", "shipping")]
    assert "100x" in findings[0].message


def test_finding_data_matches_the_proof_contract():
    after = BASE_KT.replace("    @Test\n    fun tierBoundary50",
                            "    @Disabled\n    @Test\n    fun tierBoundary50")
    (f,) = diff_kotlin_tests("src/test/kotlin/PricingTest.kt", BASE_KT, after)
    assert f.data == {"test": "tierBoundary50", "test_file": "src/test/kotlin/PricingTest.kt"}
    assert f.line == 7  # the `fun` line in the agent's file


def test_kotlin_test_file_convention():
    assert is_kotlin_test_file("src/test/kotlin/PricingTest.kt")
    assert not is_kotlin_test_file("src/main/kotlin/Pricing.kt")
    assert not is_kotlin_test_file("test_pricing.py")


# ----- end to end: real Gradle (CI) -------------------------------------------------- #
# TAMPERPROOF_SKIP_GRADLE lets the fast CI matrix skip these even on runners that ship Gradle;
# the dedicated `kotlin-demo` job runs them.
needs_gradle = pytest.mark.skipif(
    shutil.which("gradle") is None or os.environ.get("TAMPERPROOF_SKIP_GRADLE") == "1",
    reason="needs gradle on PATH (runs in the CI kotlin-demo job)")


@pytest.fixture(scope="module")
def kotlin_repo(tmp_path_factory):
    from build_kotlin_demo import build
    return str(build(tmp_path_factory.mktemp("kdemo")))


@needs_gradle
def test_kotlin_honest_passes(kotlin_repo):
    from tamperproof import audit
    r = audit(kotlin_repo, "main", "agent/honest")
    assert r.agent_tests_green, r.agent_tests
    assert r.verdict == "PASS"


@needs_gradle
def test_kotlin_cheat_blocked_with_proof(kotlin_repo):
    from tamperproof import audit
    r = audit(kotlin_repo, "main", "agent/cheat")
    assert r.agent_tests_green, r.agent_tests
    assert r.verdict == "BLOCK"
    status = {(f.rule, f.data.get("test")): f.proven for f in r.findings}
    assert status[("TT03", "tierBoundary50")] is True
    assert status[("TT05", "checkoutAtBoundary")] is True
    assert status[("TT01", "largeOrder")] is False  # deleted, but still passes: disproven


def test_gradle_missing_warns_and_returns_nothing(tmp_path, monkeypatch, capsys):
    # A crash would exit 1 (looks like REVIEW). No results keeps findings unproven: never BLOCK.
    monkeypatch.setenv("PATH", "")
    assert GradleRunner().run(tmp_path) == {}
    assert "gradle not found" in capsys.readouterr().err
