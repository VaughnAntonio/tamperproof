"""The agent under audit is an adversary: it can write any file in its tree.

Each test here is a way an agent could beat tamperproof itself, found by the repo audit
(Nov 2026). A test fails if that hole reopens.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tamperproof import audit, gitio  # noqa: E402
from tamperproof.cli import main  # noqa: E402
from tamperproof.report import to_markdown  # noqa: E402
from tamperproof.runners import GradleRunner, PytestRunner  # noqa: E402

FAKE_PASS = '<testsuites><testsuite><testcase classname="{cls}" name="{name}"/></testsuite></testsuites>'


def _repo(path: Path, commits: list[dict[str, str | None]]) -> Path:
    """A git repo with one commit per dict ({path: text}, None deletes). Tags c0, c1, ..."""
    path.mkdir(parents=True, exist_ok=True)

    def git(*a):
        subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    for i, files in enumerate(commits):
        for rel, text in files.items():
            if text is None:
                (path / rel).unlink()
            else:
                (path / rel).parent.mkdir(parents=True, exist_ok=True)
                (path / rel).write_text(text)
        git("add", "-A")
        git("commit", "-q", "--allow-empty", "-m", f"c{i}")
        git("tag", f"c{i}")
    return path


# ----- 1. results the agent wrote itself must never be read --------------------- #
def test_pytest_ignores_a_results_file_committed_by_the_agent(tmp_path):
    # conftest crashes on import, so pytest writes no XML of its own.
    (tmp_path / "conftest.py").write_text('raise ImportError("boom")\n')
    (tmp_path / "test_a.py").write_text("def test_real():\n    assert False\n")
    (tmp_path / ".tamperproof-junit.xml").write_text(FAKE_PASS.format(cls="test_a", name="test_real"))

    assert PytestRunner().run(tmp_path) == {}


def test_gradle_ignores_results_committed_by_the_agent(tmp_path, monkeypatch):
    # A `gradle` that does nothing stands in for a build that fails before tests run.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "gradle"
    fake.write_text("#!/bin/sh\nexit 1\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    tree = tmp_path / "tree"
    results = tree / "build" / "test-results" / "test"
    results.mkdir(parents=True)
    (results / "TEST-PricingTest.xml").write_text(FAKE_PASS.format(cls="PricingTest", name="largeOrder()"))

    assert GradleRunner().run(tree) == {}


# ----- 2/5. SC01 proof must check the tests that actually flipped ------------------ #
SHOP_BASE = "def label(name):\n    return 'x'\n"
SHOP_CHEAT = "def label(name):\n    if name == \"special\":\n        return \"VIP\"\n    return 'x'\n"


def test_string_special_case_is_proven(tmp_path):
    repo = _repo(tmp_path / "r", [
        {"shop.py": SHOP_BASE,
         "test_shop.py": 'from shop import label\n\ndef test_vip():\n    assert label("special") == "VIP"\n'},
        {"shop.py": SHOP_CHEAT},
    ])
    r = audit(str(repo), "c0", "c1")
    sc01 = [f for f in r.findings if f.rule == "SC01"]
    assert [f.proven for f in sc01] == [True]
    assert r.verdict == "BLOCK"


def test_flipped_test_that_never_uses_the_value_proves_nothing(tmp_path):
    # 100 is a test value only because of test_unrelated. Removing the branch breaks
    # test_wrapper, which never mentions 100, so the branch is not proven test-driven.
    wrapper = "\ndef wrapper():\n    return f(100)\n"
    src0 = "def f(x):\n    return 0\n" + wrapper
    src1 = "def f(x):\n    if x == 100:\n        return 1\n    return 0\n" + wrapper
    tests = ("from m import wrapper\n\ndef test_unrelated():\n    assert len([0] * 100) == 100\n\n"
             "def test_wrapper():\n    assert wrapper() == 1\n")
    repo = _repo(tmp_path / "r", [{"m.py": src0, "test_m.py": tests}, {"m.py": src1}])
    r = audit(str(repo), "c0", "c1")
    sc01 = [f for f in r.findings if f.rule == "SC01"]
    assert len(sc01) == 1
    assert sc01[0].proven is not True
    assert r.verdict != "BLOCK"


# ----- 3. a crash must not read as REVIEW ---------------------------------------- #
def test_crash_exits_3_not_review(tmp_path, capsys):
    repo = _repo(tmp_path / "r", [{"a.py": "x = 1\n"}])
    assert main([str(repo), "--base", "no-such-ref"]) == 3
    assert "tamperproof crashed" in capsys.readouterr().err


# ----- 4. file names git would quote ---------------------------------------------- #
def test_deleting_a_test_file_with_a_non_ascii_name_is_seen(tmp_path):
    repo = _repo(tmp_path / "r", [
        {"test_café.py": "def test_a():\n    assert 1 + 1 == 2\n"},
        {"test_café.py": None},
    ])
    r = audit(str(repo), "c0", "c1", run_proofs=False)
    assert [(f.rule, f.file) for f in r.findings] == [("TT01", "test_café.py")]


def test_all_py_files_reads_every_file_exactly(tmp_path):
    repo = _repo(tmp_path / "r", [{"a.py": "x = 1\n", "pkg/b é.py": "y = 'é'\n", "c.txt": "no"}])
    assert gitio.all_py_files(repo, "c0") == {"a.py": "x = 1\n", "pkg/b é.py": "y = 'é'\n"}


# ----- 10. agent text must not break the PR comment table ------------------------- #
def test_pipe_in_a_finding_stays_in_its_table_cell(tmp_path):
    repo = _repo(tmp_path / "r", [
        {"m.py": "def f(x):\n    return 0\n", "test_m.py": "def test_f():\n    assert 300 > 0\n"},
        {"m.py": "def f(x):\n    if x | 0 == 300:\n        return 1\n    return 0\n"},
    ])
    md = to_markdown(audit(str(repo), "c0", "c1", run_proofs=False))
    rows = [ln for ln in md.splitlines() if "SC01" in ln and ln.startswith("|")]
    assert len(rows) == 1
    assert rows[0].replace("\\|", "").count("|") == 5  # 4 cells
