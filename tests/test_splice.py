"""Per-test oracle restoration: only the tampered test functions go back to base.

Restoring whole files threw away the agent's legitimate new tests and could
break collection (for example, an import the agent legitimately renamed),
which would turn every finding in that file into a false proof.
"""

from __future__ import annotations

import ast
import subprocess
import textwrap
from pathlib import Path

from tamperproof.proof import oracle_restoration, run_pytest, splice_tests
from tamperproof.static_rules import diff_tests


def _src(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


def _fn(src: str, name: str) -> str:
    """Source of a function (module or class level) by qualified name."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and f"{node.name}.{sub.name}" == name:
                    return ast.unparse(sub)
        if isinstance(node, ast.FunctionDef) and node.name == name and node.col_offset == 0:
            return ast.unparse(node)
    raise KeyError(name)


def test_weakened_test_restored_and_new_test_kept():
    base = _src("""
        def test_a():
            assert f() == 3
        """)
    head = _src("""
        def test_a():
            assert f() > 0


        def test_new():
            assert g() == 1
        """)
    out = splice_tests(base, head, ["test_a"])
    assert "assert f() == 3" in _fn(out, "test_a")
    assert "assert g() == 1" in _fn(out, "test_new")


def test_added_skip_decorator_is_removed():
    base = _src("""
        import pytest


        def test_a():
            assert f() == 3
        """)
    head = _src("""
        import pytest


        @pytest.mark.skip(reason="flaky")
        def test_a():
            assert f() == 3
        """)
    out = splice_tests(base, head, ["test_a"])
    assert "skip" not in out


def test_deleted_test_is_appended():
    base = _src("""
        def test_a():
            assert f() == 3


        def test_gone():
            assert f() == 4
        """)
    head = _src("""
        def test_a():
            assert f() == 3
        """)
    out = splice_tests(base, head, ["test_gone"])
    assert "assert f() == 4" in _fn(out, "test_gone")
    assert "assert f() == 3" in _fn(out, "test_a")


def test_class_methods_replaced_and_reinserted():
    base = _src("""
        class TestX:
            def test_a(self):
                assert f() == 3

            def test_b(self):
                assert f() == 4
        """)
    head = _src("""
        class TestX:
            def test_a(self):
                assert True

            def test_new(self):
                assert f() == 9
        """)
    out = splice_tests(base, head, ["TestX.test_a", "TestX.test_b"])
    assert "assert f() == 3" in _fn(out, "TestX.test_a")
    assert "assert f() == 4" in _fn(out, "TestX.test_b")
    assert "assert f() == 9" in _fn(out, "TestX.test_new")


def test_missing_import_and_helper_copied_from_base():
    base = _src("""
        import math


        def _expected():
            return math.floor(3.7)


        def test_a():
            assert f() == _expected()
        """)
    head = _src("""
        def test_a():
            assert f() > 0
        """)
    out = splice_tests(base, head, ["test_a"])
    ns: dict = {"f": lambda: 3}
    exec(compile(out, "<spliced>", "exec"), ns)
    ns["test_a"]()  # NameError here means a dependency was not copied


def test_deleted_class_or_bad_source_falls_back():
    base = _src("""
        class TestX:
            def test_a(self):
                assert f() == 3
        """)
    assert splice_tests(base, "x = 1\n", ["TestX.test_a"]) is None
    assert splice_tests(base, "def broken(:\n", ["TestX.test_a"]) is None


# ----- integration: the agent's new test survives the oracle run ------------ #
def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_oracle_keeps_agent_new_test(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "mod.py").write_text("def f():\n    return 1\n")
    base_tests = "from mod import f\n\n\ndef test_a():\n    assert f() == 2\n"
    (repo / "test_m.py").write_text(base_tests)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "agent")
    head_tests = ("from mod import f\n\n\ndef test_a():\n    assert f() > 0\n\n\n"
                  "def test_new():\n    assert f() == 1\n")
    (repo / "test_m.py").write_text(head_tests)
    _git(repo, "commit", "-q", "-am", "agent")

    findings = diff_tests("test_m.py", base_tests, head_tests)
    assert any(f.rule == "TT05" for f in findings)
    work = tmp_path / "work"
    from tamperproof import gitio
    baseline = run_pytest(gitio.export_tree(str(repo), "agent", work / "head"))

    restored = oracle_restoration(str(repo), "main", "agent", findings, baseline, work)

    assert restored["test_m.test_new"] == "passed"
    assert restored["test_m.test_a"] == "failed"
    assert all(f.proven for f in findings if f.rule == "TT05")
