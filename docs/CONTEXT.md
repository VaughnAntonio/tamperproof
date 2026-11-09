# Project context and decision log

Why tamperproof exists, the design decisions behind it, and where it stands. Started 5 Nov 2026.

## Origin

- **Goal:** a unique Python project that solves a real problem for **JetBrains**, to support my applications for summer 2027 roles.
- **Research:** recent JetBrains blog posts (Oct–Nov 2026), read through the Plushcap mirror because blog.jetbrains.com only rendered one post.
- **First idea, rejected:** "Air Spend Guard", AI-agent spend analytics with anomaly detection, budget forecasting and Eco-Mode savings. I rejected it because JetBrains already ships this (*Air Governance*, *Junie Eco Mode*). Lesson: **don't rebuild what the company already sells; find an unsolved gap.**
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

- v0.1.0 done: 9 tests passing, ruff clean, CI workflow (lint, Python 3.11–3.13, demo gate), 4 shared PyCharm run configs, setup guide.
- Per-test oracle restoration done (`splice_tests`): 16 tests passing; the demo cheat branch now adds one legitimate test that the oracle run keeps.
- Test-id matching made exact (`.` boundary, parametrized ids, file and class respected): 22 tests passing.
- SC02 proof done (handler ablation + fault injection): 27 tests passing. Only a real swallowed error proves SC02; I chose this so a blind spot alone can never BLOCK (5 Nov 2026).
- SC01 range branches + spike probe done: 34 tests passing. I chose stdlib only over Hypothesis: a deterministic neighbour probe gives the same proof every run (5 Nov 2026).
- Confirmed on macOS (Python 3.13): ruff clean, 34 tests, demo PASS 0 / BLOCK 2 (6 Nov 2026).
- Calibration started (`tamperproof/calibration.py`: confusion at REVIEW/BLOCK, Wilson 95% CI, per-rule stats): 40 tests passing. I approved `pyarrow` as an optional `[calibrate]` extra only, so the audit runtime stays stdlib + pytest (6 Nov 2026).
- Calibration pipeline built, waiting for gated dataset access: 68 tests passing (6 Nov 2026). Decisions:
  - **Replay, never execute.** `replay.py` rebuilds the agent's tree from editor ops, `cat` heredocs and git restores only. Any other `.py` write marks the rollout `partial` and excludes it, so no reconstructed tree is a guess.
  - **Base = `base_commit` + altered `test_patch`** (`rebuild.py`), the sandbox as the agent found it.
  - **Static-only, stratified sample** (800 hack / 800 honest / 200 attempt / 200 other_test, ≤3 per task, seed 0) over `rollouts_truncated`. Precision is rebuilt at the population ratio from per-stratum recall and FPR; BLOCK is "not measured" until the SWE-Gym sandbox images are available.
  - Finding while building it: a single TT05 (medium, 12) is below REVIEW (15) with proofs off, so a `oneoff` hack can PASS statically. Calibration therefore also reports an `ANY` (score > 0) operating point. Weights unchanged pending the numbers.
- GitHub Action done (roadmap 5): 79 tests passing (7 Nov 2026). Composite `action.yml` runs the audit, posts one sticky PR comment (`tamperproof/github.py`, stdlib urllib, hidden marker), uploads SARIF to code scanning, then gates. Decisions:
  - **`fail-on: block` by default**, so REVIEW is a comment, not a red check (matches "only proven findings BLOCK").
  - **Dogfood** on this repo's PRs (`.github/workflows/tamperproof.yml`); the CI demo gate now goes through the action itself.
  - Fork PRs: comment and SARIF upload degrade to warnings; the verdict still gates.
  - SARIF fixes: `startLine` clamped to ≥ 1 (GitHub rejects 0); `informationUri` points at the repo.
  - Dogfooding PR #7 found that a *disproven* high-severity SC01 was a SARIF `error`, which fails GitHub's code-scanning check. SARIF level now follows proof status: proven `error`, suspected at most `warning`, disproven `note`.
- Qodana notes done (roadmap 6): 83 tests passing (7 Nov 2026). `docs/QODANA.md`, `qodana.yaml`, `.github/workflows/qodana.yml`. Decisions:
  - **Live Qodana job** (Community for Python, no token), **advisory**: no `failThreshold`; tamperproof stays the only gate.
  - **SARIF aligned with Qodana's profile**: `tool.driver.version`, `automationDetails.id` (`tamperproof/`), `partialFingerprints["tamperproof/v1"]` (rule + file + code span, not line). No `baselineState` (base-vs-head already makes every finding new).
  - Research finding: no documented way to show non-Qodana SARIF in Qodana Cloud; code scanning is the shared view. Qodana gate exits 255; tamperproof keeps 0/1/2.
- Kotlin/JUnit sketch done (roadmap 7): 101 tests, 2 Kotlin e2e skipped without Gradle (7 Nov 2026). Decisions:
  - **Real Kotlin demo** (`demo/build_kotlin_demo.py`, JUnit 5, Gradle) through the unchanged oracle restoration: honest PASS, cheat BLOCK (TT03 + TT05 proven, TT01 disproven).
  - **Stand-in detector** (`kotlin_sketch.py`): tokens with strings/comments masked, not an AST, labelled as such; the real port uses UAST (`docs/KOTLIN_PORT.md`).
  - **Gradle in CI only**, nothing installed on his Mac. `TAMPERPROOF_SKIP_GRADLE=1` keeps the fast matrix fast; the `kotlin-demo` job fails if the e2e tests skip.
  - Missing Gradle returns no results (findings stay unproven, never BLOCK) instead of crashing, because a crash exits 1 and reads as REVIEW.
  - Dogfood caught a toolchain mismatch on PR #9: the runner image's preinstalled Gradle 9.8.2 ran the Kotlin e2e inside the self-audit and they failed with Kotlin plugin 2.0.21. I chose to upgrade (Kotlin 2.4.21, CI pinned to Gradle 9.8.2) rather than skip Gradle in the dogfood run. Qodana's PEP 604 "invalid type annotation" warnings: documented only.
  - Research: `qodana.yaml` `plugins:` loads Marketplace plugins, so a tamperproof IntelliJ plugin would run inside Qodana.
- Hostile-agent fixes from a whole-repo audit: 108 tests passing (8 Nov 2026). The agent's tree is treated as an adversary. Decisions:
  - **Results files are deleted before each run** (pytest XML, Gradle `build/test-results`): a committed "passed" file used to be read as the real run.
  - **Exit 3 on crash.** Python's crash code 1 read as REVIEW and passed a `fail-on: block` gate, so one hanging test could dodge a BLOCK. Verdict codes 0/1/2 are unchanged.
  - **`core.quotePath=false`**: a test file named `test_café.py` was invisible to the audit.
  - **SC01 proof compares values in the flipped test itself.** Before, `repr()` text search cleared every double-quoted string special-case, and any test anywhere "using" the value let an unrelated failure prove it. A flip in a test that never uses the value now stays a suspicion.
  - **`pull_request` only** in the action; `pull_request_target` would run fork code with a write token.
  - Speed: one `git cat-file --batch` instead of a `git show` per file, and call sites counted once per audit.
- Free gold-patch calibration (`tamperproof/gold.py`, `docs/CALIBRATION.md`): 120 tests passing (9 Nov 2026). The gated rollouts are still 403 and the public 144-rollout release ships no tool-call content, so nothing is replayable. Decisions:
  - **No model, no key, no Docker.** Honest = the maintainers' fix on the original tests; cheats = revert, delete, skip of the changed test on the impossible tests, built by script. I chose this over a paid gpt-5-mini pilot (~$9).
  - **Static only**, so BLOCK is not measured. All 349 tasks: 14.0% honest diffs reach REVIEW (mostly `BR01`, `COV01`); delete/skip ~99%.
  - The run found TT05 missed expected values inside calls (`self.assertEqual(x, 0o755)`). Fixed: revert REVIEW 44.1% to 73.4%, honest unchanged.
  - Still open: one TT05 (12) is below REVIEW (15), so 31 reverts PASS. Weights unchanged; my call.
- Repo: `github.com/VaughnAntonio/tamperproof`.

## Known limitations (say these in interviews)

- Python + pytest for the full rule set; Kotlin/JUnit covers only test tampering, via a token-level stand-in (not UAST)
- Oracle restoration reverts only the flagged test functions (plus base imports and helpers they need). Fixtures in `conftest.py`, module-level setup and unflagged tests stay in the agent's version, so a tamper hidden there is not undone. If a clean splice is impossible, it falls back to the whole file and says so.
- SC01 covers equality and numeric range branches; the range probe needs a literal call in the tests and a module-level function
- SC02 is proven only by a real swallowed error during the tests. A suite that can't see the block stays suspected, never BLOCK
- Calibrated only on human fixes and scripted cheats, statically (`docs/CALIBRATION.md`); no real-agent precision/recall and no BLOCK rate yet

## Related docs

- `docs/SETUP_GUIDE.md`: step-by-step PyCharm setup
- `docs/QODANA.md`: running next to Qodana
- `docs/CALIBRATION.md`: gold-patch calibration results
- `docs/KOTLIN_PORT.md`: Kotlin/JUnit port sketch
