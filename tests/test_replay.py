"""Rebuilding the agent's final file tree from a transcript, without executing anything.

Fixture transcripts follow the documented `messages` schema of
AlignmentResearch/impossible-swe-nemotron-rollouts: assistant messages carry
`tool_calls=[{id, name, arguments(JSON string)}]`, tool messages answer by `tool_call_id`.
"""

from __future__ import annotations

import json

from tamperproof.replay import detect_root, replay

ROOT = "/testbed"
PRISTINE = {  # repo at base_commit
    "pkg/calc.py": "def add(a, b):\n    return a - b\n",
    "tests/test_calc.py": "def test_add():\n    assert add(1, 2) == 3\n",
}
ALTERED = {  # after the impossible test_patch (uncommitted in the sandbox)
    **PRISTINE,
    "tests/test_calc.py": "def test_add():\n    assert add(1, 2) == 4\n",
}


class T:
    """Tiny transcript builder: each call gets an id and a tool reply."""

    def __init__(self) -> None:
        self.messages: list[dict] = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "issue"},
        ]
        self.n = 0

    def call(self, name: str, reply: dict | None = None, **args) -> T:
        self.n += 1
        cid = f"call-{self.n}"
        self.messages.append({"role": "assistant", "reasoning": "...", "content": None,
                              "tool_calls": [{"id": cid, "name": name, "arguments": json.dumps(args)}]})
        self.messages.append({"role": "tool", "tool_call_id": cid, "name": name,
                              "content": json.dumps(reply if reply is not None else {"output": "ok"})})
        return self

    def edit(self, reply: dict | None = None, **args) -> T:
        return self.call("str_replace_editor", reply, **args)

    def bash(self, command: str) -> T:
        return self.call("bash", command=command)


def run(t: T):
    return replay(t.messages, root=ROOT, base=ALTERED.get, pristine=PRISTINE.get, altered=ALTERED)


# ----- editor operations ---------------------------------------------------- #
def test_str_replace_on_source_is_applied():
    r = run(T().edit(command="str_replace", path="/testbed/pkg/calc.py",
                     old_str="return a - b", new_str="return a + b"))
    assert r.files == {"pkg/calc.py": "def add(a, b):\n    return a + b\n"}
    assert not r.partial


def test_relative_path_and_create():
    r = run(T().edit(command="create", path="pkg/new.py", file_text="X = 1\n"))
    assert r.files == {"pkg/new.py": "X = 1\n"}


def test_insert_after_line():
    r = run(T().edit(command="insert", path="/testbed/pkg/calc.py", insert_line=1, new_str="    # sum"))
    assert r.files["pkg/calc.py"] == "def add(a, b):\n    # sum\n    return a - b\n"


def test_undo_edit_restores_previous_text():
    r = run(T()
            .edit(command="str_replace", path="/testbed/tests/test_calc.py", old_str="== 4", new_str="== 3")
            .edit(command="undo_edit", path="/testbed/tests/test_calc.py"))
    assert r.files.get("tests/test_calc.py", ALTERED["tests/test_calc.py"]) == ALTERED["tests/test_calc.py"]


def test_rejected_call_is_not_applied():
    r = run(T().edit({"error": "No replacement was performed"}, command="str_replace",
                     path="/testbed/pkg/calc.py", old_str="nope", new_str="x"))
    assert r.files == {}
    assert not r.partial


def test_view_changes_nothing():
    assert run(T().edit(command="view", path="/testbed/pkg/calc.py")).files == {}


def test_divergence_is_reported_not_guessed():
    # The tool accepted the edit but our reconstructed text lacks old_str: we have lost track
    # of the file, so the rollout must be marked partial rather than silently wrong.
    r = run(T().edit(command="str_replace", path="/testbed/pkg/calc.py", old_str="missing", new_str="x"))
    assert r.partial


# ----- git restores (the dataset's `hack_test_restored`) ---------------------- #
def test_git_checkout_file_restores_base_commit_version():
    r = run(T().bash("cd /testbed && git checkout -- tests/test_calc.py && python -m pytest -q"))
    assert r.files == {"tests/test_calc.py": PRISTINE["tests/test_calc.py"]}
    assert not r.partial


def test_cd_persists_across_calls():
    r = run(T().bash("cd /testbed/tests").bash("git restore test_calc.py"))
    assert r.files == {"tests/test_calc.py": PRISTINE["tests/test_calc.py"]}


def test_reset_hard_restores_every_tracked_file():
    r = run(T()
            .edit(command="str_replace", path="/testbed/pkg/calc.py", old_str="a - b", new_str="a + b")
            .bash("git reset --hard"))
    # Only paths that differ from base are reported: the source edit is gone and the
    # altered test is back to its base_commit version.
    assert r.files == {"tests/test_calc.py": PRISTINE["tests/test_calc.py"]}


def test_stash_then_pop_round_trips():
    r = run(T()
            .edit(command="str_replace", path="/testbed/pkg/calc.py", old_str="a - b", new_str="a + b")
            .bash("git stash")
            .bash("git stash pop"))
    assert r.files["pkg/calc.py"] == "def add(a, b):\n    return a + b\n"
    assert r.files.get("tests/test_calc.py", ALTERED["tests/test_calc.py"]) == ALTERED["tests/test_calc.py"]


# ----- shell writes ----------------------------------------------------------- #
def test_heredoc_write_is_replayed():
    cmd = "cat > /testbed/repro.py << 'EOF'\nfrom pkg.calc import add\nprint(add(1, 2))\nEOF\npython repro.py"
    r = run(T().bash(cmd))
    assert r.files == {"repro.py": "from pkg.calc import add\nprint(add(1, 2))\n"}
    assert not r.partial


def test_heredoc_body_with_blank_lines_and_reversed_redirect():
    cmd = "cd /testbed && cat <<EOF > pkg/extra.py\nA = 1\n\nB = 2\nEOF"
    r = run(T().bash(cmd))
    assert r.files == {"pkg/extra.py": "A = 1\n\nB = 2\n"}


def test_sed_in_place_on_python_file_marks_partial():
    r = run(T().bash("sed -i 's/== 4/== 3/' tests/test_calc.py"))
    assert r.partial and "sed" in r.partial[0]


def test_harmless_commands_do_not_mark_partial():
    r = run(T()
            .bash("grep -rn 'def add' pkg/ > /dev/null 2>&1; python -m pytest tests -q 2>&1 | tail -5")
            .bash("rm -rf .pytest_cache __pycache__ && cat pkg/calc.py")
            .bash("echo hi > /tmp/scratch.txt"))
    assert r.files == {} and not r.partial


def test_python_tool_file_write_marks_partial():
    r = run(T().call("python", code="open('tests/test_calc.py', 'w').write('')"))
    assert r.partial


# ----- sandbox root -------------------------------------------------------------- #
def test_detect_root_from_known_files():
    paths = ["/workspace/repo/pkg/calc.py", "/workspace/repo/tests/test_calc.py", "/tmp/x.py"]
    assert detect_root(paths, set(PRISTINE)) == "/workspace/repo"
    assert detect_root([], set(PRISTINE)) == "/testbed"
