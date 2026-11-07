"""Kotlin/JUnit test-tampering detector: a STAND-IN for a PSI/UAST implementation (roadmap 7).

This module scans tokens, not a syntax tree. That breaks the project's "AST, not regex"
rule on purpose. Python's stdlib has no Kotlin parser, and the point of this sketch is
to show that the *proof* stage carries over to Kotlin unchanged. A real port would
build these same findings from IntelliJ's UAST (`UMethod`, `UAnnotation`,
`UCallExpression`). See docs/KOTLIN_PORT.md.

What it does to stay honest:
- Strings and comments are masked before scanning, so a `{` or `@Test` inside a string
  can't fool it.
- It emits the same `Finding`s and the same `data` contract (`test`, `test_file`) as
  `static_rules.diff_tests`, so `proof.oracle_restoration` runs unmodified.
- Only TT01, TT02, TT03, TT05 and TT06 are covered. SC01/SC02 need resolved
  expressions (a real parser), so Kotlin source changes raise no findings here.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .static_rules import Finding

TEST_ANNOTATIONS = {"Test", "ParameterizedTest", "RepeatedTest"}
SKIP_ANNOTATIONS = {"Disabled", "Ignore"}  # JUnit 5, JUnit 4

_FUN = re.compile(r"\bfun\s+(?:<[^>]*>\s*)?(`[^`]+`|\w+)\s*\(")
_ANNOTATION = re.compile(r"@(?:\w+:)?(\w+)")
_ASSERT = re.compile(r"\bassert\w*\s*\(")


def is_kotlin_test_file(path: str) -> bool:
    """Gradle convention (`src/test/...`) or a `*Test.kt` / `*Tests.kt` name."""
    if not path.endswith(".kt"):
        return False
    stem = path.rsplit("/", 1)[-1].removesuffix(".kt")
    return "/src/test/" in f"/{path}" or stem.endswith(("Test", "Tests"))


def _mask(src: str) -> str:
    """Same-length copy with string, char and comment contents blanked (newlines kept)."""
    out = list(src)
    i, n = 0, len(src)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(i, j)
            i = j
        elif src.startswith('"""', i):
            j = src.find('"""', i + 3)
            j = n if j < 0 else j + 3
            blank(i + 3, j - 3)
            i = j
        elif src[i] in "\"'":
            q, j = src[i], i + 1
            while j < n and src[j] != q and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            blank(i + 1, j)
            i = j + 1
        else:
            i += 1
    return "".join(out)


def _close(masked: str, start: int, open_ch: str, close_ch: str) -> int:
    """Index just past the bracket matching the one at `start`."""
    depth = 0
    for k in range(start, len(masked)):
        if masked[k] == open_ch:
            depth += 1
        elif masked[k] == close_ch:
            depth -= 1
            if depth == 0:
                return k + 1
    return len(masked)


def _split_args(masked: str, src: str, a: int, b: int) -> list[str]:
    """Top-level comma split of src[a:b], using the mask to ignore nested commas."""
    parts, depth, last = [], 0, a
    for k in range(a, b):
        c = masked[k]
        if c in "([{<":
            depth += 1
        elif c in ")]}>":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append(src[last:k])
            last = k + 1
    parts.append(src[last:b])
    return [" ".join(p.split()) for p in parts if p.strip()]


@dataclass
class _Fn:
    line: int
    annotations: set[str]
    asserts: list[tuple[str, list[str]]]  # (call name, args)

    @property
    def calls(self) -> list[str]:
        return [f"{name}({', '.join(args)})" for name, args in self.asserts]


def _test_functions(src: str | None) -> dict[str, _Fn]:
    if not src:
        return {}
    masked = _mask(src)
    out: dict[str, _Fn] = {}
    for m in _FUN.finditer(masked):
        # Annotations sit between the previous statement/brace and `fun`.
        prefix_start = max(masked.rfind(ch, 0, m.start()) for ch in "{};") + 1
        annotations = set(_ANNOTATION.findall(masked[prefix_start:m.start()]))
        if not annotations & TEST_ANNOTATIONS:
            continue
        params_end = _close(masked, m.end() - 1, "(", ")")
        brace = masked.find("{", params_end)
        eq = masked.find("=", params_end)
        if brace >= 0 and (eq < 0 or brace < eq):
            body = (brace, _close(masked, brace, "{", "}"))
        else:
            nl = masked.find("\n", params_end)
            body = (params_end, len(masked) if nl < 0 else nl)
        asserts = []
        for a in _ASSERT.finditer(masked, *body):
            end = _close(masked, a.end() - 1, "(", ")")
            name = masked[a.start():a.end() - 1].strip()
            asserts.append((name, _split_args(masked, src, a.end(), end - 1)))
        name = src[m.start(1):m.end(1)].strip("`")
        out[name] = _Fn(masked.count("\n", 0, m.start()) + 1, annotations, asserts)
    return out


def _delta(args: list[str]) -> float | None:
    try:
        return float(args[2].rstrip("fF")) if len(args) == 3 else None
    except ValueError:
        return None


def diff_kotlin_tests(path: str, before: str | None, after: str | None) -> list[Finding]:
    old, new = _test_functions(before), _test_functions(after)
    out: list[Finding] = []
    for name, fn in old.items():
        data = {"test": name, "test_file": path}
        if name not in new:
            out.append(Finding("TT01", "high", path, fn.line, f"`{name}` was deleted", data))
            continue
        nf = new[name]
        if nf.annotations & SKIP_ANNOTATIONS and not fn.annotations & SKIP_ANNOTATIONS:
            out.append(Finding("TT03", "high", path, nf.line, f"`{name}` is now @Disabled/@Ignore", data))

        # TT06: same assertEquals(expected, actual, delta) with a bigger delta.
        old_eq = {tuple(a[:2]): a for n, a in fn.asserts if n == "assertEquals" and _delta(a)}
        loosened = set()
        for n, a in nf.asserts:
            prev = old_eq.get(tuple(a[:2])) if n == "assertEquals" else None
            if prev and (d_new := _delta(a)) and d_new > (d_old := _delta(prev)):
                loosened |= {f"assertEquals({', '.join(prev)})", f"assertEquals({', '.join(a)})"}
                out.append(Finding("TT06", "medium", path, nf.line,
                                   f"`{name}` tolerance loosened {d_old:g} -> {d_new:g} "
                                   f"({d_new / d_old:.0f}x)", data))

        old_calls = Counter(c for c in fn.calls if c not in loosened)
        new_calls = Counter(c for c in nf.calls if c not in loosened)
        removed, added = list((old_calls - new_calls).elements()), list((new_calls - old_calls).elements())
        if sum(new_calls.values()) < sum(old_calls.values()):
            out.append(Finding("TT02", "high", path, nf.line,
                               f"`{name}` went from {len(fn.calls)} to {len(nf.calls)} assertions", data))
        if removed and added:
            out.append(Finding("TT05", "medium", path, nf.line,
                               f"`{name}` expectation changed: `{removed[0]}` -> `{added[0]}`", data))
    return out
