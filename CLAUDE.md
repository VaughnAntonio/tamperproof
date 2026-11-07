# tamperproof

Python tool that **proves**, by running experiments, whether an AI coding agent made tests pass by fixing the code or by gaming the tests. Portfolio project by Vaughn, aimed at JetBrains.

Background, decisions and current status: @docs/CONTEXT.md

## Commands

```bash
# once, inside .venv
python -m pip install -e ".[dev]"
# 101 tests (2 Kotlin e2e skip without gradle); builds a demo repo in tmp, needs git on PATH
pytest -q
# lint (rules pinned in pyproject.toml)
ruff check .
# delete demo_repo/ first if it exists
python demo/build_demo_repo.py demo_repo
# expect PASS, exit 0
tamperproof demo_repo --base main --head agent/honest
# expect BLOCK, exit 2
tamperproof demo_repo --base main --head agent/cheat --sarif cheat.sarif --md cheat.md
```

Run `ruff check .` and `pytest -q` after every change, and report the results. Exit code 2 from the cheat audit is correct, not a failure.

## Architecture

- `tamperproof/static_rules.py`: AST diff of base vs head; emits `Finding(rule, severity, file, line, message, data)`. Rules: TT01–TT06 (test tampering), SC01–SC03, API01, COV01, BR01.
- `tamperproof/proof.py`: runs experiments in `git archive` copies.
  - Oracle restoration (TT*): original tampered test functions spliced into the agent's test file (`splice_tests`; whole-file fallback) + agent's new code. Original fails while agent version passes → `proven=True`. Still passes → `proven=False`.
  - Branch ablation (SC01 equality): delete the suspicious `if`, rerun agent tests. Tests using that literal flip to failing → `proven=True`.
  - Spike probe (SC01 `kind: range`, severity low): call the function at each literal test input and its neighbours, with and without the branch. Differs only at the test input → `proven=True`; differs at a neighbour → `proven=False` (real tier); no literal call → stays suspected. Stdlib only, deterministic.
  - Handler ablation + fault injection (SC02): re-raise in the new handler; a passing test that now fails → `proven=True` (real error swallowed). Else inject a fault into the `try`: a test that notices → `proven=False`; none notice, or no test runs the block → stays suspected (`proven=None`). Fault injection alone never proves, so a blind spot can't BLOCK.
- `tamperproof/runners.py`: `PytestRunner` / `GradleRunner` (picked by `build.gradle(.kts)`), shared JUnit XML parser. `kotlin_sketch.py`: token-level stand-in detector for Kotlin tests (TT01/02/03/05/06), same `Finding.data` contract; oracle restoration restores whole files for non-`.py`.
- `tamperproof/audit.py`: orchestration, score, verdict. `report.py`: text, Markdown, SARIF 2.1.0. `cli.py`: argparse.
- `action.yml` (composite) + `tamperproof/github.py`: sticky PR comment (stdlib urllib, hidden `<!-- tamperproof -->` marker), SARIF upload, `fail-on` gate. Never fails the job over a comment; the exit code gates.
- `Finding.data` is the contract between static rules and the proof engine (`test`, `test_file`, `line`, `literals`, `try_line`, `kind`). For SC02, `line` is the handler and `try_line` its `try`. For SC01, `kind: "range"` marks a `<`/`<=`/`>`/`>=` branch (absent for equality). Document any new key.

## Rules that must not change without asking Vaughn

1. **Only a proven finding can produce `BLOCK`.** Static suspicion alone → at most `REVIEW` (score ≥ 15). Disproven findings score 0.
2. **Never skip, delete or weaken a test to make the suite pass.** This project exists to catch exactly that. If a test is wrong, explain why first.
3. Exit codes stay `0` PASS / `1` REVIEW / `2` BLOCK.

## Conventions

- Python ≥ 3.11 (`tarfile` `filter="data"`). Standard library only at runtime besides pytest. Ask before adding a dependency.
- `from __future__ import annotations`, type hints, small pure functions, docstrings that explain *why*.
- Every new rule needs: an entry in `RULES`, a unit test with a minimal before/after snippet, and (if provable) a proof experiment plus a demo case in `demo/build_demo_repo.py`.
- Commits: Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`). Work on a branch, not `main`.
- If you add a common workflow, add a shared PyCharm run config in `.run/<name>.run.xml`.

## Working with Vaughn

- He uses **PyCharm, free tier** (unified PyCharm; no Pro features). When suggesting IDE steps, give the menu path plus shortcut as *Windows/Linux / macOS*. Don't assume remote interpreters, the database tools, the profiler or coverage UI.
- The proof engine runs pytest in a **subprocess**, so PyCharm breakpoints inside the demo tests don't hit. Inspect `restored`, `baseline` and `ablated` in `proof.py` instead.
- Lead with the answer; explain the *why* at master's level; state limitations honestly (they are part of the JetBrains pitch).

## Roadmap (next tasks, in order)

1. ~~Per-test oracle restoration~~ (done, `splice_tests` in `proof.py`)
2. ~~Fault injection to prove SC02~~ (done, `swallow_experiments` in `proof.py`)
3. ~~Perturbation for SC01 beyond equality~~ (done, stdlib `spike_probe` in `proof.py`; Hypothesis not needed)
4. Calibrate on `AlignmentResearch/impossible-swe-nemotron-rollouts`; report precision/recall
5. ~~PR-comment GitHub Action (`--md`) and SARIF upload~~ (done, `action.yml` + `tamperproof/github.py`)
6. ~~Qodana integration notes~~ (done, `docs/QODANA.md` + advisory Qodana workflow)
7. ~~Kotlin/JUnit port sketch via IntelliJ PSI~~ (done, `docs/KOTLIN_PORT.md`, Gradle runner + Kotlin demo in CI)
