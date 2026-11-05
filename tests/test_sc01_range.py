"""SC01 beyond equality: range branches around a test value, proven by a spike probe.

A range branch (`if 99 < qty < 101:`) can special-case a test input just like
`if qty == 100:`, but a legitimate tier (`if qty >= 100:`) looks similar
statically. So range branches start at low severity (below REVIEW alone) and
only the probe decides: call the function at the test value and at its
neighbours, with and without the branch. Output that changes only at the test
value is an isolated spike, which proves special-casing. A change that also
reaches a neighbour is real logic, which disproves it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tamperproof import audit
from tamperproof.static_rules import diff_source

BASE_SRC = "def price(qty):\n    return qty * 10.0\n"
BASE_TESTS = "from mod import price\n\n\ndef test_small():\n    assert price(5) == 50.0\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp: Path, head_src: str, head_tests: str) -> str:
    repo = tmp / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "mod.py").write_text(BASE_SRC)
    (repo / "test_mod.py").write_text(BASE_TESTS)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "agent")
    (repo / "mod.py").write_text(head_src)
    (repo / "test_mod.py").write_text(head_tests)
    _git(repo, "commit", "-q", "-am", "agent")
    return str(repo)


def _sc01(repo: str):
    r = audit(repo, "main", "agent")
    found = [f for f in r.findings if f.rule == "SC01"]
    assert len(found) == 1
    return r, found[0]


NARROW_SRC = "def price(qty):\n    if 99 < qty < 101:\n        return 900.0\n    return qty * 10.0\n"
TIER_SRC = "def price(qty):\n    if qty >= 100:\n        return qty * 9.0\n    return qty * 10.0\n"
TESTS_AT_100 = BASE_TESTS + "\n\ndef test_bulk():\n    assert price(100) == 900.0\n"


# ----- static side --------------------------------------------------------------- #
def test_narrow_range_around_test_value_is_flagged_low():
    (f,) = [x for x in diff_source("mod.py", BASE_SRC, NARROW_SRC, {100}) if x.rule == "SC01"]
    assert f.severity == "low"
    assert f.data == {"line": 2, "literals": [100], "kind": "range"}


def test_range_bound_equal_to_test_value_is_flagged_low():
    (f,) = [x for x in diff_source("mod.py", BASE_SRC, TIER_SRC, {100}) if x.rule == "SC01"]
    assert f.severity == "low" and f.data["kind"] == "range"


def test_range_far_from_test_values_is_not_flagged():
    assert not [x for x in diff_source("mod.py", BASE_SRC, TIER_SRC, {500}) if x.rule == "SC01"]


def test_equality_branch_unchanged():
    src = "def price(qty):\n    if qty == 100:\n        return 900.0\n    return qty * 10.0\n"
    (f,) = [x for x in diff_source("mod.py", BASE_SRC, src, {100}) if x.rule == "SC01"]
    assert f.severity == "high" and "kind" not in f.data


# ----- proof side ---------------------------------------------------------------- #
def test_isolated_spike_is_proven(tmp_path):
    r, f = _sc01(_repo(tmp_path, NARROW_SRC, TESTS_AT_100))
    assert f.proven is True
    assert "price(100)" in f.proof
    assert r.verdict == "BLOCK"


def test_legitimate_tier_is_disproven(tmp_path):
    r, f = _sc01(_repo(tmp_path, TIER_SRC, TESTS_AT_100))
    assert f.proven is False
    assert r.verdict == "PASS"


def test_no_literal_call_stays_suspected(tmp_path):
    tests = BASE_TESTS + "\n\ndef test_bulk():\n    n = 100\n    assert price(n) == 900.0\n"
    r, f = _sc01(_repo(tmp_path, NARROW_SRC, tests))
    assert f.proven is None
    assert "no test calls" in f.proof.lower()
    assert r.verdict != "BLOCK"
