# tamperproof and Qodana

Qodana checks **code quality**. tamperproof checks **test integrity**: did the agent make CI green by fixing the code or by gaming the tests? Neither can see what the other sees, so they belong in the same pipeline.

All Qodana facts below come from the Qodana 2026.2 docs (sources at the end). Anything the docs don't state is marked as such.

## What works today

This repo runs both on every pull request:

| | Qodana | tamperproof |
|---|---|---|
| Workflow | `.github/workflows/qodana.yml` | `.github/workflows/tamperproof.yml` |
| Config | `qodana.yaml` (`linter: qodana-python-community`) | action inputs |
| Token | none (Community linters only need `QODANA_TOKEN` to view reports in Qodana Cloud) | none |
| PR comment | yes (`post-pr-comment`) | yes (one sticky comment) |
| SARIF to code scanning | `upload-sarif`, category `qodana` | `upload-sarif`, category `tamperproof` |
| Gates the PR? | no (advisory: no `failThreshold`) | yes, only on a proven `BLOCK` |

**Where they meet is GitHub code scanning.** Both upload SARIF 2.1.0 with `github/codeql-action/upload-sarif`, so a reviewer sees Qodana's inspections and tamperproof's findings in one Security tab, filtered by tool.

### tamperproof's SARIF follows Qodana's profile

Qodana's output (`qodana.sarif.json`) carries a few fields that make reports stable across runs. tamperproof now emits the equivalents:

| Field | Qodana | tamperproof |
|---|---|---|
| `tool.driver.version` | linter version | package version |
| `automationDetails.id` | per-run id | `tamperproof/` (matches the upload category) |
| `partialFingerprints` | `equalIndicator/v1` | `tamperproof/v1`: hash of rule + file + the finding's code span, **not** the line, so an alert keeps its identity when code moves |
| `level` | from IDE severity | from **proof**: proven `error`, suspected at most `warning`, disproven `note` |

The last row is the important difference. Qodana's level says how bad a problem would be. tamperproof's level says how sure we are that it happened. GitHub fails the code scanning check on any `error`, so only a proven finding may emit one. That mirrors the tool's core rule: only proof can BLOCK.

Not emitted: `baselineState`. Qodana compares against a stored baseline; tamperproof already compares base against head, so every finding is "new" by construction. `versionControlProvenance` is left out because the audit sees refs, not the remote URL.

## Gaps (honest)

1. **No documented way to show tamperproof's findings in Qodana itself.** `qodana send` uploads "the report (qodana.sarif.json and other analysis results)" of a Qodana run to Qodana Cloud. No page I found documents importing SARIF from a non-Qodana tool. So today the shared view is GitHub code scanning, not Qodana Cloud.
2. **Different exit-code contracts.** Qodana's quality gate (`failThreshold`, or `failureConditions.severityThresholds`) exits with **255** when the problem count exceeds the threshold. tamperproof exits 0 / 1 / 2 (PASS / REVIEW / BLOCK). A problem count is the wrong model for tamperproof: one proven tamper must block, while a hundred disproven suspicions must not.
3. **`bootstrap` is not used.** `qodana.yaml`'s `bootstrap` key runs setup commands, but the docs don't say when it runs or whether a non-zero exit fails the run. tamperproof therefore runs as its own step, where exit codes are well defined.

## Proposed deeper integration

In order of effort:

1. **External SARIF in Qodana Cloud** (on JetBrains' side). If Qodana Cloud accepted a second SARIF per run, tamperproof findings would sit next to inspections in the report and in the IDE's Qodana tab, with no plugin. The SARIF is already aligned (above).
2. **A proof-aware quality gate** (needs item 1). Map tamperproof to Qodana's gate by counting only *proven* findings: `severityThresholds.critical: 0` over results whose `level` is `error`. That is exactly `BLOCK`, expressed in Qodana's own terms.
3. **tamperproof as a Qodana / IntelliJ inspection.** The static stage (AST diff of tests and source) maps onto inspections over the PSI; the proof stage needs the sandbox that Air already provides for agent runs. This is also the route to Kotlin/JUnit (roadmap step 7).

## Limitations of this setup

- Qodana is advisory here on purpose. Making it a second gate would mix a quality threshold with tamperproof's proof rule.
- `pr-mode` (on by default) makes Qodana analyse only the files a PR changed; tamperproof also looks only at the diff, but runs the whole test suite for its proofs.
- Qodana Community for Python is the free linter. The paid `qodana-python` linter and test-coverage thresholds were not tried.

## Sources

- [Qodana: Configuration reference (`qodana.yaml`)](https://www.jetbrains.com/help/qodana/qodana-yaml.html)
- [Qodana: GitHub Actions](https://www.jetbrains.com/help/qodana/github.html)
- [Qodana: Quality gate](https://www.jetbrains.com/help/qodana/quality-gate.html)
- [Qodana: Linters](https://www.jetbrains.com/help/qodana/linters.html)
- [Qodana: Output files (SARIF)](https://www.jetbrains.com/help/qodana/qodana-sarif-output.html)
- [JetBrains/qodana-cli README (`qodana send`)](https://github.com/JetBrains/qodana-cli/blob/main/README.md)
- [GitHub: Uploading a SARIF file](https://docs.github.com/en/code-security/how-tos/scan-code-for-vulnerabilities/integrate-with-existing-tools/uploading-a-sarif-file-to-github)
