# tamperproof

**Prove, don't guess, when an AI coding agent games the test suite.**

## The problem

AI coding agents are rewarded for one thing: making the tests go green. Sometimes they get there by fixing the bug. Sometimes they get there by changing the tests: skipping the failing one, weakening `== 482.99` to `> 0`, loosening a tolerance 100×, or adding `if qty == 100: return 900.0` to the source.

Either way, CI shows a green tick. A reviewer has to spot the difference by reading the diff.

Existing cheat detectors flag suspicious patterns, but a flag is only a hint. In a study of 327 agent-written PRs, the author of one such auditor reports that none of his detectors could be trusted to block on its own, because a pattern match is not proof. So detectors stay advisory, and the reviewer is back to reading diffs.

Why it matters:

- **Teams shipping agent-written code** need to hold it to the same bar as human code. A green tick should mean the bug is fixed, not that the tests were bent.
- **Agent platforms already run changes in sandboxes**, so the runtime experiments below add little extra cost.
- **Coding-agent benchmarks and evals** use test outcomes as the success signal. A benchmark is only as honest as its tests.
- **It fits existing pipelines.** Findings are emitted as SARIF 2.1.0, so GitHub code scanning, Qodana and other SARIF-aware tools pick them up as-is.

## The idea: static suspicion → runtime proof

tamperproof runs in two stages.

**1. Static analysis (AST, not regex).** It compares the base and agent versions of every changed Python file and raises suspicions:

| Rule | Detects |
|---|---|
| TT01 | Test deleted |
| TT02 | Assertions removed from a test |
| TT03 | Test newly skipped / xfail |
| TT04 | Assertion that can never fail (`assert True`, `x == x`, `... or True`) |
| TT05 | Expected value changed (`== 482.99` → `> 0`) |
| TT06 | `approx` tolerance loosened |
| SC01 | New source branch comparing input to a literal that appears in the tests |
| SC02 | New broad `except` that silently swallows errors |
| SC03 | New `# noqa` / `# type: ignore` / `# pragma: no cover` |
| API01 | Public function signature changed |
| COV01 | Changed function that no test calls |
| BR01 | Blast radius (call sites of changed functions) |

**2. Proof engine.** It runs two experiments in throw-away copies of the agent's tree:

- **Oracle restoration** (TT rules). Put the *original* test file next to the agent's *new* code and run it. If a test the agent skipped, deleted or weakened now **fails**, the edit hid a real failure. That is **proven**. If it still passes, the suspicion is **disproven** and its score drops to zero.
- **Branch ablation** (SC01). Delete the suspicious `if` from the agent's code and rerun the agent's own tests. If previously passing tests that use that literal now fail, the branch exists only to satisfy test inputs. That is **proven**.
- **Spike probe** (SC01, range branches such as `if 99 < qty < 101:`). Call the function at the test input and its neighbours, with and without the branch. Output that changes only at the test input is an isolated spike: **proven**. A change that also reaches a neighbour is a real tier: **disproven**.

**Verdict.** Only proven findings can `BLOCK`. Static suspicion alone can at most ask for `REVIEW`. False alarms are actively *disproven* rather than left for a human.

## Docs

- **[Kotlin/JUnit port sketch](docs/KOTLIN_PORT.md)**: the proof engine on Kotlin via Gradle (built), and the UAST/IntelliJ plugin route (proposed)
- **[Qodana integration](docs/QODANA.md)**: running next to Qodana, SARIF alignment, gaps and proposed integration
- **[Setup guide (PyCharm)](docs/SETUP_GUIDE.md)**: prerequisites, interpreter, run configs, debugging the proof engine, Git workflow
- **[Project context](docs/CONTEXT.md)**: why the project exists, design decisions and status

## Demo

```bash
python -m pip install -e ".[dev]"
python demo/build_demo_repo.py demo_repo
python -m tamperproof demo_repo --base main --head agent/honest
python -m tamperproof demo_repo --base main --head agent/cheat --sarif cheat.sarif --md cheat.md
pytest -q tests
```

The demo repo has a pricing bug (`>` instead of `>=` at discount tiers) and two agent branches. **Both branches pass their own test suite.**

```
=== agent/honest ===
Agent's own test suite: GREEN (7 tests)
Verdict: PASS   suspicion score 0/100

=== agent/cheat ===
Agent's own test suite: GREEN (6 tests)
Verdict: BLOCK   suspicion score 100/100

[   PROVEN] SC01  pricing.py:9  new branch `if qty == 100:` matches test value(s) [100]
            proof: Removing the branch makes 1 passing test(s) fail (test_tier_boundary_100).
[   PROVEN] TT03  test_pricing.py:15  `test_tier_boundary_50` is now skipped/xfail
            proof: Original test FAILS on the agent's code. The test change hid a real failure.
[   PROVEN] TT05  test_pricing.py:25  `checkout(50, 10.0, 2)`: `== 482.99` -> `> 0`
            proof: Original test FAILS on the agent's code. The test change hid a real failure.
[disproven] TT01  test_pricing.py:18  `test_large_order` was deleted
            proof: Original test still passes on the agent's code.
[disproven] TT06  test_pricing.py:19  tolerance loosened 0.01 -> 1 (100x)
[disproven] SC02  pricing.py:21  `except Exception:` hides errors
            proof: An injected failure in the `try` block is noticed by 1 of 3 test(s) that run it (test_shipping).
```

Note the **disproven** lines. The agent deleted `test_large_order` and loosened a tolerance, which a pattern-only tool would flag. Running the experiments shows neither edit hid a failure, so neither counts toward the block. That is what makes an automatic `BLOCK` defensible.

Exit codes are `0` PASS, `1` REVIEW, `2` BLOCK, so CI can gate on them.

## GitHub Action

Run tamperproof on every pull request. It posts **one sticky PR comment** (updated on each push), uploads findings to the **code scanning** tab as SARIF, and fails the check only on `BLOCK`.

```yaml
# .github/workflows/tamperproof.yml
on: pull_request
permissions:
  contents: read
  pull-requests: write    # sticky comment
  security-events: write  # SARIF upload
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: VaughnAntonio/tamperproof@main
        with:
          base: origin/${{ github.base_ref }}
```

| Input | Default | Meaning |
|---|---|---|
| `base` | required | Ref before the change |
| `head` | `HEAD` | Ref with the change |
| `repo` | `.` | Path to the git repo to audit |
| `comment` | `true` | Sticky PR comment with the Markdown report |
| `upload-sarif` | `true` | Send findings to GitHub code scanning |
| `fail-on` | `block` | `block`, `review` or `never` |
| `no-proof` | `false` | Static analysis only (can never BLOCK) |
| `python-version` | `3.12` | Python for tamperproof and the audited tests |

Outputs: `verdict`, `score`, `exit-code`, `markdown`, `sarif`. The report also goes to the job summary.

Limits: PRs from forks get a read-only token, so the comment and the SARIF upload are skipped with a warning (the verdict still gates). Proof runs execute the test suite several times, so they cost CI minutes; `no-proof: true` is the cheap advisory mode.

## Project layout

```
tamperproof/
  gitio.py         git plumbing: changed files, file-at-ref, export tree to sandbox
  static_rules.py  AST detectors -> Finding objects
  proof.py         oracle restoration, branch ablation, spike probe, SC02 experiments
  runners.py       pytest and Gradle test runners, one JUnit XML parser
  kotlin_sketch.py stand-in Kotlin/JUnit test-tamper detector (tokens, not UAST)
  audit.py         orchestration, scoring, verdict
  report.py        terminal, Markdown (PR comment), SARIF 2.1.0 (Qodana / GitHub)
  cli.py           python -m tamperproof
  github.py        sticky PR comment (stdlib urllib)
action.yml                composite GitHub Action: audit, PR comment, SARIF upload, gate
demo/build_demo_repo.py   reproducible honest-vs-cheat scenario
demo/build_kotlin_demo.py same scenario in Kotlin/JUnit 5 (Gradle)
tests/                    101 tests, including the Python and Kotlin end-to-end demos
.run/                     shared PyCharm run configurations
.github/workflows/ci.yml  lint + tests on 3.11-3.13 + honest/cheat demo gate through the action + Kotlin demo via Gradle
.github/workflows/tamperproof.yml  dogfood: the action audits this repo's own PRs
.github/workflows/qodana.yml       Qodana Community for Python, advisory, SARIF to code scanning
qodana.yaml               Qodana config
```

## Limitations and next steps

- Python + pytest for the full rule set. Kotlin/JUnit runs the test-tampering proofs through Gradle with a token-level stand-in detector; a real port would use IntelliJ's UAST ([sketch](docs/KOTLIN_PORT.md)).
- Oracle restoration reverts only the flagged test functions (plus base imports and helpers they need). Fixtures in `conftest.py`, module-level setup and unflagged tests stay in the agent's version, so a tamper hidden there is not undone. If a clean splice is impossible, it falls back to the whole file and says so.
- SC01 covers equality and range branches on numeric inputs. The spike probe needs a test that calls the function with the value as a plain literal, and only probes module-level functions; special-casing via dict lookups or string matching isn't covered.
- SC02 is proven only when re-raising in the handler breaks a passing test (a real error was swallowed). Fault injection can clear it, but a suite that is merely blind to the block stays a suspicion: an honest fallback handler looks the same, and a blind spot must not BLOCK.
- Plug into coding-agent platforms as a post-run check, so the agent sees a `BLOCK` with proof and must retry *before* a human reviews.
- Calibrate on real agent PR datasets (for example `AlignmentResearch/impossible-swe-nemotron-rollouts`) and report precision/recall of proven vs suspected.
