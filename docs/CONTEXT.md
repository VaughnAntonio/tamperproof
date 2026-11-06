# Project context and decision log

This file carries over the planning conversation (claude.ai, 5 Nov 2026) in which tamperproof was designed and built. `CLAUDE.md` imports it, so Claude Code starts every session with this background.

## Origin

- **Goal:** a unique Python project that solves a real problem for **JetBrains**, to support Vaughn's applications for summer 2027 roles.
- **Research:** recent JetBrains blog posts (Oct–Nov 2026), read through the Plushcap mirror because blog.jetbrains.com only rendered one post.
- **First idea, rejected:** "Air Spend Guard", AI-agent spend analytics with anomaly detection, budget forecasting and Eco-Mode savings. Vaughn rejected it because JetBrains already ships this (*Air Governance*, *Junie Eco Mode*). Lesson: **don't rebuild what the company already sells; find an unsolved gap.**
- **Chosen idea:** tamperproof. AI agents sometimes reach "green CI" by gaming the tests (skipping, weakening assertions, hardcoding test inputs). Existing detectors only *flag patterns*. One author who mined 327 agent PRs reported that no detector could be trusted to block on its own. tamperproof turns suspicion into **runtime proof**.

## Why it fits JetBrains

- *"Our 2026 Direction: AI and Classic Workflows"*: AI code must get the same scrutiny as human code, and changes must be visible and reversible.
- *"How Cloud-Based Agent Sandboxing Works in Air"*: agents already run in sandboxes, so the proof experiments are cheap to add.
- *"Coding-Agent Benchmarks Should Match Their Users' Task Flows"* (JetBrains Research): benchmarks use tests as the success signal, so gamed tests corrupt them.
- **Qodana** ingests SARIF, which tamperproof outputs.

## Key design decisions

| Decision | Why |
|---|---|
| AST diff, not text diff | Robust to formatting; can tell which assertion changed and how |
| Two stages: static suspicion → runtime proof | Pattern matching alone is a hint; proof makes an automatic block defensible |
| Oracle restoration | Original tests + agent code: a failure proves the test edit hid a real bug |
| Branch ablation | Removing a hardcoded `if` and seeing tests fail proves it exists only for test inputs |
| Only proven findings can BLOCK | Avoids false-positive blocks that would make teams turn the tool off |
| Disproven findings score 0 | Shows the tool actively clears false alarms (for example, a deleted test that still passes) |
| Exit codes 0/1/2 + SARIF + Markdown | Drops straight into CI, Qodana and PR comments |
| Stdlib + pytest only | Easy to run anywhere; nothing to explain in an interview |

## Demo scenario (`demo/build_demo_repo.py`)

The pricing module has a tier-boundary bug (`>` instead of `>=`).

- `agent/honest`: fixes `>=` and adds an edge-case test → CI green → **PASS 0/100**
- `agent/cheat`: `if qty == 100: return 900.0`, skips `test_tier_boundary_50`, weakens `== 482.99` to `> 0`, loosens tolerance 100×, deletes `test_large_order`, adds a silent `except Exception` → CI green → **BLOCK 100/100**
  - Proven: SC01, TT03, TT05 (checkout)
  - Disproven: TT01 (deleted test still passes), TT06 and TT05 (shipping tolerance), SC02 (`test_shipping` notices an injected fault)

## Status (5 Nov 2026)

- v0.1.0 done: 9 tests passing, ruff clean, CI workflow (lint, Python 3.11–3.13, demo gate), 4 shared PyCharm run configs, setup guide, Claude Project instructions.
- Per-test oracle restoration done (`splice_tests`): 16 tests passing; the demo cheat branch now adds one legitimate test that the oracle run keeps.
- Test-id matching made exact (`.` boundary, parametrized ids, file and class respected): 22 tests passing.
- SC02 proof done (handler ablation + fault injection): 27 tests passing. Only a real swallowed error proves SC02; Vaughn chose this so a blind spot alone can never BLOCK (5 Nov 2026).
- SC01 range branches + spike probe done: 34 tests passing. Vaughn chose stdlib only over Hypothesis: a deterministic neighbour probe gives the same proof every run (5 Nov 2026).
- Confirmed on macOS (Python 3.13): ruff clean, 34 tests, demo PASS 0 / BLOCK 2 (6 Nov 2026).
- Calibration started (`tamperproof/calibration.py`: confusion at REVIEW/BLOCK, Wilson 95% CI, per-rule stats): 40 tests passing. Vaughn approved `pyarrow` as an optional `[calibrate]` extra only, so the audit runtime stays stdlib + pytest (6 Nov 2026).
- Calibration pipeline built, waiting for gated dataset access: 68 tests passing (6 Nov 2026). Vaughn's choices:
  - **Replay, never execute.** `replay.py` rebuilds the agent's tree from editor ops, `cat` heredocs and git restores only. Any other `.py` write marks the rollout `partial` and excludes it, so no reconstructed tree is a guess.
  - **Base = `base_commit` + altered `test_patch`** (`rebuild.py`), the sandbox as the agent found it.
  - **Static-only, stratified sample** (800 hack / 800 honest / 200 attempt / 200 other_test, ≤3 per task, seed 0) over `rollouts_truncated`. Precision is rebuilt at the population ratio from per-stratum recall and FPR; BLOCK is "not measured" until the SWE-Gym sandbox images are available.
  - Finding while building it: a single TT05 (medium, 12) is below REVIEW (15) with proofs off, so a `oneoff` hack can PASS statically. Calibration therefore also reports an `ANY` (score > 0) operating point. Weights unchanged pending the numbers.
- Repo: `github.com/VaughnAntonio/tamperproof`.

## Known limitations (say these in interviews)

- Python + pytest only
- Oracle restoration reverts only the flagged test functions (plus base imports and helpers they need). Fixtures in `conftest.py`, module-level setup and unflagged tests stay in the agent's version, so a tamper hidden there is not undone. If a clean splice is impossible, it falls back to the whole file and says so.
- SC01 covers equality and numeric range branches; the range probe needs a literal call in the tests and a module-level function
- SC02 is proven only by a real swallowed error during the tests. A suite that can't see the block stays suspected, never BLOCK
- No calibration on real agent data yet, so no precision/recall numbers

## Related docs

- `docs/SETUP_GUIDE.md`: Vaughn's step-by-step PyCharm + Claude Code setup
- `docs/CLAUDE_PROJECT_INSTRUCTIONS.md`: same context formatted for a claude.ai Project (interview prep, explanations)
