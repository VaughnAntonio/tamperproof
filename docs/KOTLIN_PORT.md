# Porting tamperproof to Kotlin/JUnit (sketch)

**Claim:** tamperproof's design does not depend on Python. The *proof* stage only needs "run the tests, read JUnit XML", and it already runs on Kotlin in this repo. Only the *static* stage needs a language front end, and on the JVM that front end is IntelliJ's UAST.

This page separates what is **built and tested** here from what is **proposed**. IntelliJ, Gradle and Qodana facts come from their docs (sources at the end).

## What is built (runs in CI)

| Piece | File | Status |
|---|---|---|
| Pluggable test runner | `tamperproof/runners.py` | `PytestRunner` (unchanged behaviour) and `GradleRunner`, chosen by the presence of `build.gradle(.kts)` |
| One JUnit XML parser | `runners.parse_junit_xml` | pytest and Gradle both write JUnit XML; Gradle's `name()` brackets are stripped so ids are `PricingTest.tierBoundary50` |
| Stand-in detector | `tamperproof/kotlin_sketch.py` | TT01, TT02, TT03 (`@Disabled` / `@Ignore`), TT05, TT06 (`assertEquals` delta) |
| Oracle restoration | `proof.oracle_restoration` | **unchanged logic**; for non-Python files it restores the whole original test file |
| Kotlin demo | `demo/build_kotlin_demo.py` | same pricing bug as the Python demo, JUnit 5, Gradle |
| CI gate | `ci.yml` job `kotlin-demo` | honest exits 0 (PASS), cheat exits 2 (BLOCK) |

On the Kotlin cheat branch the audit proves `@Disabled tierBoundary50` (TT03) and the weakened `checkoutAtBoundary` (TT05), and *disproves* the deleted `largeOrder` (TT01) because the original still passes on the agent's code. That is the same verdict, by the same experiment, as the Python demo.

### The stand-in is not the port

`kotlin_sketch.py` scans tokens (with strings and comments masked), not a syntax tree. That breaks the project's "AST, not regex" rule on purpose: Python's standard library has no Kotlin parser, and the point here is the proof stage. It emits the same `Finding` objects and the same `data` contract (`test`, `test_file`) as the Python detector, which is why `proof.py` didn't need to change. Its known blind spots, all fixed by UAST:

- No resolution: an aliased import (`import ...Disabled as Off`) or a custom meta-annotation is missed.
- `assertEquals` arguments are compared as text, so a reformatted but equal assertion reads as a change.
- Test identity is `ClassName.function`, matched against the file path, so a class whose name differs from its file isn't matched (the finding stays unproven, never wrongly proven).
- No SC01 / SC02: special-casing and swallowed errors need real expressions.

## The real port: rule by rule

UAST is "an abstraction layer on the PSI of different programming languages targeting the JVM", with full support for Java and Kotlin. One implementation therefore covers both languages.

| Rule | Python today | Kotlin / JUnit form | UAST approach | Effort |
|---|---|---|---|---|
| TT01 test removed | `ast` functions named `test*` | `@Test fun` gone | `UMethod` with a test annotation, matched by class + name | direct |
| TT02 assertions removed | count `assert` nodes | count `assert*` / `assertThat` calls | `UCallExpression` with resolved JUnit/AssertJ/kotlin.test owner | direct |
| TT03 skipped | `pytest.mark.skip/xfail` | `@Disabled`, `@Ignore`, `assumeTrue(false)` | annotations on the `UMethod`, resolved by qualified name (catches aliases) | direct |
| TT04 always true | `assert True`, `x == x` | `assertTrue(true)`, `assertEquals(x, x)` | constant evaluation of the argument expressions | direct |
| TT05 expectation changed | compare operator/right side | `assertEquals(expected, actual)` arg 0 changed, or `assertEquals` replaced by `assertTrue(... > 0)` | match calls by the `actual` argument's expression | changes |
| TT06 tolerance loosened | `pytest.approx(abs=)` | `assertEquals(e, a, delta)`, AssertJ `isCloseTo(x, within(d))` | numeric literal of the delta argument | direct |
| SC01 special-casing | `if x == <literal>` | `if (x == 100)`, **and `when (x) { 100 -> ... }`** | `UIfExpression` plus Kotlin `when` branches, literal from the tests | changes |
| SC02 swallowed error | broad `except` | `catch (e: Exception) {}`, `runCatching { }.getOrDefault(...)` | catch clauses + `runCatching` calls | changes |
| SC03 suppression | `# noqa`, `# type: ignore` | `@Suppress(...)`, `//noinspection` | annotations + comments | direct |

**Two trees per file.** The detector diffs base against head. In the IDE, the head version is the real file. The base version can be built as an in-memory PSI file with `PsiFileFactory.createFileFromText()` from `git show base:path`, then converted to UAST like any other file.

**Type resolution.** Resolving which `assertEquals` or which `Disabled` a call refers to is what removes the stand-in's blind spots. For Kotlin, that resolution goes through the Kotlin compiler's analysis layer (the Analysis API in K2-mode IDEs). I didn't verify the exact recommended entry point in its docs, so treat this row as a pointer, not a spec.

## The proof stage on the JVM

| Experiment | Python | JVM | Effort |
|---|---|---|---|
| Run tests + read results | pytest `--junitxml` | `gradle test`; JUnit XML lands in `build/test-results/test`, one file per class | **done** |
| Oracle restoration | per-test splice via `ast` | whole-file restore (done); per-test splice = replace one `UMethod`'s text range in the head file | done / changes |
| Branch ablation (SC01) | drop the `if` via `ast` | delete the `UIfExpression` / `when` branch from a copy, recompile | changes |
| Handler ablation + fault injection (SC02) | rewrite the handler | rewrite the `catch` body to rethrow / inject `throw` in the `try` | changes |
| Spike probe (SC01 ranges) | `importlib` + call the function | needs a JVM call harness: compile, then call the function reflectively (or via a generated JUnit test) at the input and its neighbours | **hard** |

Cost is the real difference: each JVM experiment is a Gradle build. Gradle's daemon and build cache help, and in JetBrains Air the agent's sandbox has already compiled the project, so experiments could reuse that build instead of starting cold.

## Where it would live

1. **IntelliJ inspection plugin (static stage).** A local inspection built on `AbstractBaseUastLocalInspectionTool`, registered with `language="UAST"` on the `com.intellij.localInspection` extension point, highlights tampering in the editor as the agent (or a person) edits a test. Problems are reported with `ProblemsHolder.registerUProblem()`.
2. **Qodana (CI).** `qodana.yaml` can load Marketplace plugins: `plugins: - id: <plugin.id>`, "downloaded and invoked during inspection". Published on JetBrains Marketplace, the same plugin would run in Qodana with no extra integration. That closes the static half of the gap from [QODANA.md](QODANA.md). The docs also warn that an inspection ID that isn't registered in the active profile is silently ignored, so the plugin's inspections would have to be enabled in the profile.
3. **Air (proof stage).** The proof experiments need to run tests, which an IDE inspection should not do on the fly. They fit as a post-run check in the agent sandbox: the agent sees `BLOCK` with proof and retries before a human reviews.

## Limitations

- The built part covers the test-tampering rules only, through a token-level stand-in, not UAST.
- Toolchain pinned to GitHub's ubuntu-24.04 image (Gradle 9.8.2, Kotlin plugin 2.4.21). The first version used Kotlin 2.0.21 and passed under a pinned Gradle 8.10.2, but this repo's own dogfood audit ran the same tests under the image's preinstalled Gradle 9.8.2 and reported the suite red: the self-audit caught a toolchain mismatch.
- Only JUnit 5 on Gradle with the default `test` task and results directory is exercised. Maven (Surefire also writes JUnit XML), Android and custom test tasks are not.
- Parametrized JUnit tests (`@ParameterizedTest`) report display names per case; matching them to the `fun` name is untested.
- Gradle runs only in CI here (Vaughn's choice: nothing installed locally), so the Kotlin end-to-end tests are skipped on a laptop without Gradle, and say so.
- Building the actual plugin needs IntelliJ IDEA with the Plugin DevKit, not PyCharm.

## Sources

- [IntelliJ Platform SDK: UAST](https://plugins.jetbrains.com/docs/intellij/uast.html)
- [IntelliJ Platform SDK: PSI Files](https://plugins.jetbrains.com/docs/intellij/psi-files.html)
- [IntelliJ Platform SDK: Code Inspections](https://plugins.jetbrains.com/docs/intellij/code-inspections.html)
- [Qodana: Configuration reference (`plugins`)](https://www.jetbrains.com/help/qodana/qodana-yaml.html)
- [Gradle: Testing in Java & JVM projects (test reports)](https://docs.gradle.org/current/userguide/java_testing.html)
- [Kotlin Analysis API](https://kotlin.github.io/analysis-api/)
