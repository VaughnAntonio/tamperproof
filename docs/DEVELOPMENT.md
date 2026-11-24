# Development guide

How to set up tamperproof locally, run the demo and tests, and find your way around the code.

## Prerequisites

| What | Why | Check |
|---|---|---|
| **Git** ≥ 2.30 | tamperproof and its tests call `git` directly | `git --version` |
| **Python** 3.11.4+ (3.12 recommended) | Runs the project | `python --version` |
| **Gradle** + JDK 17+ (optional) | Only for the Kotlin end-to-end tests; they skip without it | `gradle --version` |

## Install

```bash
git clone https://github.com/VaughnAntonio/tamperproof.git
cd tamperproof
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
```

`-e` (editable) means code changes take effect without reinstalling. `[dev]` adds `ruff`. For calibration work, also install `[calibrate]` (adds `pyarrow`).

Check it worked:

```bash
tamperproof --help
pytest -q
ruff check .
```

Expected: a usage message, all tests passing (the 2 Kotlin end-to-end tests skip without Gradle), and `All checks passed!`.

## Run the demo

```bash
python demo/build_demo_repo.py demo_repo
python -m tamperproof demo_repo --base main --head agent/honest   # PASS, exit 0
python -m tamperproof demo_repo --base main --head agent/cheat --sarif cheat.sarif --md cheat.md   # BLOCK, exit 2
```

Exit code 2 on the cheat branch is correct: it is how CI fails a cheating pull request. Delete `demo_repo/` before rebuilding it.

## Shared run configurations (PyCharm)

`.run/` holds shared run configurations that PyCharm loads automatically (dropdown next to ▶):

| Configuration | What it does |
|---|---|
| **1 Build demo repo** | Creates `demo_repo/` with `main`, `agent/honest`, `agent/cheat` |
| **2 Audit honest branch** | `Verdict: PASS` (exit 0) |
| **3 Audit cheat branch** | `Verdict: BLOCK` (exit 2), writes `cheat.sarif` and `cheat.md` |
| **4–6 Calibrate …** | Download, smoke run and summary for [calibration](CALIBRATION.md) |
| **All tests** | The pytest suite in the test runner |

Other editors: the commands above are all the configurations run.

## Debugging the proof engine

The fastest way to understand the project is to stop inside a proof:

1. Build the demo repo.
2. In `tamperproof/proof.py`, set a breakpoint in `oracle_restoration` on the line after `restored = (runner or PytestRunner()).run(tree)`, and one in `branch_ablation` after `flipped = [...]`.
3. Debug `python -m tamperproof demo_repo --base main --head agent/cheat`.
4. At the first stop, `restored` holds the **original** tests run against the agent's code: `test_tier_boundary_50: failed` is the proof.
5. At the second, `flipped` contains `test_tier_boundary_100`, because removing `if qty == 100` made it fail.

Breakpoints inside `demo_repo/` won't trigger: the proof engine runs pytest in a subprocess. Inspecting the result dicts is usually enough; otherwise enable your debugger's "attach to subprocess" option.

## Code tour

Suggested reading order: `cli.py` → `audit.py` → `static_rules.py` (`diff_tests`, then `diff_source`) → `proof.py` → `report.py`. The [README](../README.md#project-layout) has the full layout and [DESIGN.md](DESIGN.md) the reasoning.

## Contributing

1. Branch from `main`.
2. Add a failing test first, then the change.
3. Run `pytest -q` and `ruff check .` before pushing.
4. Open a pull request. CI runs lint, tests on Python 3.11–3.13, the honest/cheat demo gate, the Kotlin demo via Gradle, Qodana (advisory), and tamperproof on its own diff.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Tests fail with `FileNotFoundError: 'git'` | Install Git and restart your terminal or IDE so it sees the new `PATH` |
| `ModuleNotFoundError: tamperproof` | Run `python -m pip install -e ".[dev]"` inside the activated `.venv` |
| `TypeError: extractall() got an unexpected keyword argument 'filter'` | Python is older than 3.11.4; upgrade and recreate `.venv` |
| `fatal: destination path 'demo_repo' already exists` | Delete `demo_repo/` and rebuild |
| Windows: PowerShell refuses to activate the venv | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, once |
