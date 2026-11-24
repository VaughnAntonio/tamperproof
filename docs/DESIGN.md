# Design notes

Why tamperproof works the way it does, and the decisions behind each part.

## The gap

AI coding agents sometimes reach green CI by gaming the tests: skipping them, weakening assertions, or hard-coding test inputs in the source. Existing detectors only *flag patterns*. One author who mined 327 agent-written PRs reported that none of his detectors could be trusted to block on its own. tamperproof turns suspicion into **runtime proof**, so a block can be automatic.

## Core decisions

| Decision | Why |
|---|---|
| AST diff, not text diff | Robust to formatting; can tell which assertion changed and how |
| Two stages: static suspicion → runtime proof | Pattern matching alone is a hint; proof makes an automatic block defensible |
| Oracle restoration | Original tests + agent code: a failure proves the test edit hid a real bug |
| Branch ablation | Removing a hard-coded `if` and seeing tests fail proves it exists only for test inputs |
| Only proven findings can BLOCK | False-positive blocks make teams turn a tool off |
| Disproven findings score 0 | The tool actively clears false alarms (for example, a deleted test that still passes) |
| Exit codes 0/1/2 (+3 on crash), SARIF, Markdown | Drops straight into CI, code scanning and PR comments |
| Stdlib + pytest only at runtime | Runs anywhere, small attack surface, nothing to install in CI |

## Demo scenario (`demo/build_demo_repo.py`)

The pricing module has a tier-boundary bug (`>` instead of `>=`).

- `agent/honest`: fixes `>=` and adds an edge-case test → CI green → **PASS 0/100**
- `agent/cheat`: `if qty == 100: return 900.0`, skips `test_tier_boundary_50`, weakens `== 482.99` to `> 0`, loosens a tolerance 100×, deletes `test_large_order`, adds a silent `except Exception` → CI green → **BLOCK 100/100**
  - Proven: SC01, TT03, TT05 (checkout)
  - Disproven: TT01 (deleted test still passes), TT06 and TT05 (shipping tolerance), SC02 (`test_shipping` notices an injected fault)

## Proof engine

- **Per-test oracle restoration** (`splice_tests`). Only the flagged test functions are reverted, plus the base imports and helpers they need, so a legitimate new test the agent added still runs.
- **Exact test-id matching.** `.` boundaries, parametrized ids, file and class are all respected, so `test_a` never matches `test_ab`.
- **SC02** (silent `except`) uses handler ablation and fault injection. Only a real swallowed error proves it; a test suite that is merely blind to the block stays a suspicion, so a blind spot can never BLOCK.
- **SC01 range branches** use a deterministic neighbour probe instead of property-based testing, so the same input gives the same proof every run.

## Treating the agent as an adversary

The agent's tree is untrusted input. Fixes from a whole-repo audit:

- **Results files are deleted before each run** (pytest XML, Gradle `build/test-results`). A committed "passed" file would otherwise be read as the real run.
- **Exit 3 on crash.** Python's default crash code 1 would read as REVIEW and pass a `fail-on: block` gate, so one hanging test could dodge a BLOCK.
- **`core.quotePath=false`**, so a test file named `test_café.py` isn't invisible to the audit.
- **SC01 proof compares values inside the flipped test itself.** A flip in a test that never uses the literal stays a suspicion, so an unrelated failure can't prove it.
- **`pull_request` only** in the action. `pull_request_target` would run a fork's code with a write token.
- Speed: one `git cat-file --batch` instead of a `git show` per file, and call sites counted once per audit.

## GitHub Action

Composite `action.yml`: run the audit, post one sticky PR comment (`tamperproof/github.py`, stdlib `urllib`, hidden marker), upload SARIF to code scanning, then gate.

- **`fail-on: block` by default**, so REVIEW is a comment, not a red check.
- **Dogfooded** on this repo's own PRs (`.github/workflows/tamperproof.yml`); the CI demo gate also goes through the action.
- Fork PRs: the comment and SARIF upload degrade to warnings; the verdict still gates.
- **SARIF level follows proof status**: proven `error`, suspected at most `warning`, disproven `note`. Dogfooding found that a *disproven* high-severity finding emitted as `error` failed GitHub's code-scanning check.
- `startLine` is clamped to ≥ 1, because GitHub rejects 0.

## Qodana

Qodana runs next to tamperproof as an **advisory** job (no `failThreshold`), so tamperproof stays the only gate. The SARIF output carries `tool.driver.version`, `automationDetails.id` and a `partialFingerprints["tamperproof/v1"]` hash of rule + file + code span (not line). Details in [QODANA.md](QODANA.md).

## Kotlin/JUnit

- A **real Kotlin demo** (`demo/build_kotlin_demo.py`, JUnit 5, Gradle) runs through the unchanged oracle restoration: honest PASS, cheat BLOCK (TT03 and TT05 proven, TT01 disproven).
- The detector (`kotlin_sketch.py`) is a labelled **stand-in**: tokens with strings and comments masked, not an AST. A real port would use UAST ([KOTLIN_PORT.md](KOTLIN_PORT.md)).
- **Gradle runs in CI only.** `TAMPERPROOF_SKIP_GRADLE=1` keeps the fast matrix fast; the `kotlin-demo` job fails if the end-to-end tests skip.
- Missing Gradle returns no results (findings stay unproven, never BLOCK) instead of crashing.
- The self-audit caught a toolchain mismatch: the runner image's preinstalled Gradle 9.8.2 failed with Kotlin plugin 2.0.21. Fixed by upgrading to Kotlin 2.4.21 and pinning CI to Gradle 9.8.2.

## Calibration

- **Rollout replay** (`replay.py`, `rebuild.py`) rebuilds an agent's tree from editor ops, `cat` heredocs and git restores only. Any other `.py` write marks the rollout `partial` and excludes it, so no reconstructed tree is a guess. Waiting on gated dataset access.
- **Gold-patch calibration** (`gold.py`, [CALIBRATION.md](CALIBRATION.md)) needs no model, key or Docker. Honest = the maintainers' fix; cheats = revert, delete or skip of the changed test, built by script. Static only, so BLOCK is not measured.
- The run found TT05 missed expected values inside calls (`self.assertEqual(x, 0o755)`). Fixing it raised revert recall at REVIEW from 44.1% to 73.4%, with honest diffs unchanged.
- `pyarrow` is an optional `[calibrate]` extra, so the audit runtime stays stdlib + pytest.

## Open questions

- One TT05 (score 12) is below the REVIEW threshold (15), so a single reverted expected value can PASS statically (31 of the gold reverts). Weights are unchanged until real-agent numbers exist.
- `BR01` and `COV01` cause most honest-diff false positives at REVIEW (14.0%).

## Known limitations

- Python + pytest for the full rule set; Kotlin/JUnit covers only test tampering, via a token-level stand-in (not UAST).
- Oracle restoration reverts only the flagged test functions. Fixtures in `conftest.py`, module-level setup and unflagged tests stay in the agent's version, so a tamper hidden there is not undone. If a clean splice is impossible, it falls back to the whole file and says so.
- SC01 covers equality and numeric range branches; the range probe needs a literal call in the tests and a module-level function.
- SC02 is proven only by a real swallowed error during the tests.
- Calibrated only on human fixes and scripted cheats, statically; no real-agent precision/recall and no BLOCK rate yet.
