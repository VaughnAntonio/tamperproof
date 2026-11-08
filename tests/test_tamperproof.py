import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "demo"))

from build_demo_repo import build  # noqa: E402

from tamperproof import audit  # noqa: E402
from tamperproof.report import to_sarif  # noqa: E402
from tamperproof.static_rules import (  # noqa: E402
    call_counts,
    changed_functions,
    coverage_and_blast,
    diff_source,
    diff_tests,
)


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    return str(build(tmp_path_factory.mktemp("demo")))


@pytest.fixture(scope="module")
def honest(repo):
    return audit(repo, "main", "agent/honest")


@pytest.fixture(scope="module")
def cheat(repo):
    return audit(repo, "main", "agent/cheat")


# ----- the core claim: CI is green on both, only one is honest -------------- #
def test_both_branches_have_green_ci(honest, cheat):
    assert honest.agent_tests_green
    assert cheat.agent_tests_green


def test_honest_fix_passes(honest):
    assert honest.verdict == "PASS"
    assert not any(f.proven for f in honest.findings)


def test_cheat_is_blocked_with_proof(cheat):
    assert cheat.verdict == "BLOCK"
    proven = {f.rule for f in cheat.findings if f.proven}
    assert {"TT03", "TT05", "SC01"} <= proven


def test_removed_test_that_still_passes_is_disproven(cheat):
    # test_large_order was deleted, but the agent's code still passes it,
    # so deleting it did not hide a failure.
    tt01 = [f for f in cheat.findings if f.rule == "TT01"]
    assert tt01 and tt01[0].data["test"] == "test_large_order"
    assert tt01[0].proven is False


def test_weakened_checkout_assert_is_proven(cheat):
    tt05 = [f for f in cheat.findings if f.rule == "TT05" and "checkout" in f.message]
    assert tt05 and tt05[0].proven is True


# ----- unit tests for static rules --------------------------------------------- #
def test_trivial_assert_detected():
    before = "def test_a():\n    assert f() == 3\n"
    after = "def test_a():\n    assert True\n"
    rules = {f.rule for f in diff_tests("test_x.py", before, after)}
    assert "TT04" in rules


def test_tolerance_loosened():
    before = "def test_a():\n    assert f() == approx(1, rel=0.01)\n"
    after = "def test_a():\n    assert f() == approx(1, rel=0.5)\n"
    assert "TT06" in {f.rule for f in diff_tests("test_x.py", before, after)}


def test_hardcoded_branch_needs_test_literal():
    before = "def f(x):\n    return x\n"
    after = "def f(x):\n    if x == 1234:\n        return 7\n    return x\n"
    assert diff_source("m.py", before, after, {1234})
    assert not diff_source("m.py", before, after, {999})


def test_sarif_is_valid_shape(cheat):
    s = json.loads(to_sarif(cheat))
    assert s["version"] == "2.1.0"
    assert s["runs"][0]["properties"]["verdict"] == "BLOCK"
    assert all("ruleId" in r for r in s["runs"][0]["results"])


def test_coverage_and_blast_radius():
    old = "def price(q):\n    return q\n\ndef ship(w):\n    return w\n"
    new = "def price(q):\n    return q * 2\n\ndef ship(w):\n    return w + 1\n"
    sources = {"shop.py": new,
               "cart.py": "from shop import price\n" + "price(1)\n" * 5,
               "one.py": "import shop\nshop.ship(1)\n",
               "test_shop.py": "from shop import price\nprice(9)\n"}
    found = {(f.rule, f.severity, f.message) for f in
             coverage_and_blast("shop.py", changed_functions(old, new), sources["test_shop.py"],
                                call_counts(sources))}
    assert found == {
        ("BR01", "low", "`price` has 5 call sites"),
        ("COV01", "medium", "`ship` changed but no test calls it"),
        ("BR01", "info", "`ship` has 1 call site(s)"),
    }
