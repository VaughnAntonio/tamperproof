"""SC02 (broad exception swallowed): handler ablation plus fault injection.

Only handler ablation can prove SC02: re-raising inside the new handler makes
a passing test fail, so the handler swallows a real error the tests trigger.
Fault injection never proves. It either clears the finding (a test notices an
injected failure) or leaves it suspected with evidence that the suite is blind
there. A blind spot alone must not BLOCK an honest PR.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tamperproof import audit
from tamperproof.static_rules import diff_source

BASE_SRC = "def f(x):\n    return 10 / x\n"
SWALLOW_SRC = ("def f(x):\n    try:\n        return 10 / x\n"
               "    except Exception:\n        return 0\n")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp: Path, base_tests: str, head_src: str, head_tests: str) -> str:
    repo = tmp / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "mod.py").write_text(BASE_SRC)
    (repo / "test_mod.py").write_text(base_tests)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "agent")
    (repo / "mod.py").write_text(head_src)
    (repo / "test_mod.py").write_text(head_tests)
    _git(repo, "commit", "-q", "-am", "agent")
    return str(repo)


def _sc02(repo: str):
    r = audit(repo, "main", "agent")
    found = [f for f in r.findings if f.rule == "SC02"]
    assert len(found) == 1
    return r, found[0]


TEST_EXACT = "from mod import f\n\n\ndef test_f():\n    assert f(2) == 5\n"
TEST_WEAK = "from mod import f\n\n\ndef test_f():\n    assert f(2) >= 0\n"


def test_sc02_finding_carries_try_location():
    (f,) = [x for x in diff_source("mod.py", BASE_SRC, SWALLOW_SRC, set()) if x.rule == "SC02"]
    assert f.data == {"line": 4, "try_line": 2}


def test_real_swallowed_error_is_proven(tmp_path):
    head_tests = TEST_EXACT + "\n\ndef test_zero():\n    assert f(0) == 0\n"
    r, f = _sc02(_repo(tmp_path, TEST_EXACT, SWALLOW_SRC, head_tests))
    assert f.proven is True
    assert "test_zero" in f.proof
    assert r.verdict == "BLOCK"


def test_fault_noticed_by_a_test_is_disproven(tmp_path):
    _, f = _sc02(_repo(tmp_path, TEST_EXACT, SWALLOW_SRC, TEST_EXACT))
    assert f.proven is False
    assert "test_f" in f.proof


def test_blind_suite_stays_suspected(tmp_path):
    r, f = _sc02(_repo(tmp_path, TEST_WEAK, SWALLOW_SRC, TEST_WEAK))
    assert f.proven is None
    assert "none notice" in f.proof
    assert r.verdict != "BLOCK"


def test_unexercised_block_stays_suspected(tmp_path):
    head_src = BASE_SRC + ("\n\ndef g():\n    try:\n        return 1 / 0\n"
                           "    except Exception:\n        pass\n")
    r, f = _sc02(_repo(tmp_path, TEST_EXACT, head_src, TEST_EXACT))
    assert f.proven is None
    assert "no test runs" in f.proof.lower()
    assert r.verdict != "BLOCK"
