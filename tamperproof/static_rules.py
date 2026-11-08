"""
Static (AST-level) detectors.

These produce *suspicions*, not verdicts. Each finding carries enough location
data for the proof engine to try to confirm it at runtime.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from dataclasses import dataclass, field

SEVERITY_WEIGHT = {"high": 30, "medium": 12, "low": 4, "info": 0}

RULES = {
    "TT01": "Test removed",
    "TT02": "Assertions removed from a test",
    "TT03": "Test skipped or marked xfail",
    "TT04": "Trivially true assertion added",
    "TT05": "Expected value in an assertion changed",
    "TT06": "Numeric tolerance loosened",
    "SC01": "Source branches on a value taken from the tests",
    "SC02": "Broad exception swallowed",
    "SC03": "Lint/type suppression added",
    "API01": "Public function signature changed",
    "COV01": "Changed function not referenced by any test",
    "BR01": "Wide blast radius",
}


@dataclass
class Finding:
    rule: str
    severity: str
    file: str
    line: int
    message: str
    data: dict = field(default_factory=dict)  # extra info for the proof engine
    proven: bool | None = None                # None = not tested, True/False = proof result
    proof: str = ""

    @property
    def title(self) -> str:
        return RULES[self.rule]


def is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py") or "/tests/" in f"/{path}"


def _parse(src: str | None) -> ast.Module | None:
    if not src:
        return None
    try:
        return ast.parse(src)
    except SyntaxError:
        return None


def functions(tree: ast.Module | None) -> dict[str, ast.FunctionDef]:
    """Qualified name -> function node, for module and class level functions."""
    out: dict[str, ast.FunctionDef] = {}
    if tree is None:
        return out
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[f"{node.name}.{sub.name}"] = sub
    return out


# --------------------------------------------------------------------------- #
# Test-side detectors
# --------------------------------------------------------------------------- #
def _assert_nodes(fn: ast.AST) -> list[ast.AST]:
    nodes = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Assert):
            nodes.append(n)
        elif isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name.startswith("assert") or name == "raises":
                nodes.append(n)
    return nodes


def _is_skipped(fn: ast.FunctionDef) -> bool:
    for d in fn.decorator_list:
        s = ast.unparse(d)
        if re.search(r"\b(skip|skipif|xfail)\b", s):
            return True
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and re.search(r"\b(skip|xfail)$", ast.unparse(n.func)):
            return True
    return False


def _is_trivial(a: ast.AST) -> bool:
    if not isinstance(a, ast.Assert):
        return False
    t = a.test
    if isinstance(t, ast.Constant):
        return bool(t.value)
    if isinstance(t, ast.BoolOp) and isinstance(t.op, ast.Or):
        return any(isinstance(v, ast.Constant) and v.value for v in t.values)
    if isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], ast.Eq):
        return ast.unparse(t.left) == ast.unparse(t.comparators[0])
    # `assert result > 0` / `is not None` replacing an exact check is weak, not trivial;
    # TT05 reports it when it replaces an equality on the same expression.
    return False


OPS = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=",
       ast.Is: "is", ast.IsNot: "is not", ast.In: "in", ast.NotIn: "not in"}


def _compare_parts(a: ast.AST) -> tuple[str, str] | None:
    """For `assert <left> <op> <right>` return (left, "<op> <right>") source strings."""
    if isinstance(a, ast.Assert) and isinstance(a.test, ast.Compare) and len(a.test.ops) == 1:
        op = OPS.get(type(a.test.ops[0]), "?")
        return ast.unparse(a.test.left), f"{op} {ast.unparse(a.test.comparators[0])}"
    return None


def _is_literal(n: ast.AST) -> bool:
    try:
        ast.literal_eval(n)
        return True
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False


def _call_parts(a: ast.AST, src: str) -> tuple[str, str] | None:
    """For `self.assertEqual(x, 3)` or `assert np.allclose(x, 3)` return
    ("self.assertEqual(x)", "3"): the call with its expressions, and its expected values.

    Expected values are the literal arguments, wherever they sit (`assertEqual(3, x)` too).
    With no literal, the first argument is the subject and the rest are the expectation.
    """
    call = a.test if isinstance(a, ast.Assert) else a
    if not isinstance(call, ast.Call) or not call.args:
        return None
    subject = [n for n in call.args if not _is_literal(n)]
    expected = [n for n in call.args if _is_literal(n)]
    if not expected:
        subject, expected = call.args[:1], call.args[1:]
    if not expected:
        return None
    text = [" ".join((ast.get_source_segment(src, n) or ast.unparse(n)).split()) for n in expected]
    return f"{ast.unparse(call.func)}({', '.join(map(ast.unparse, subject))})", ", ".join(text)


def _max_tolerance(fn: ast.AST) -> float:
    best = 0.0
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and ast.unparse(n.func).endswith("approx"):
            for kw in n.keywords:
                if kw.arg in ("rel", "abs") and isinstance(kw.value, ast.Constant):
                    best = max(best, float(kw.value.value))
    return best


def diff_tests(path: str, before: str | None, after: str | None) -> list[Finding]:
    old, new = functions(_parse(before)), functions(_parse(after))
    old_tests = {k: v for k, v in old.items() if k.rsplit(".", 1)[-1].startswith("test")}
    new_tests = {k: v for k, v in new.items() if k.rsplit(".", 1)[-1].startswith("test")}
    out: list[Finding] = []

    for name, fn in old_tests.items():
        data = {"test": name, "test_file": path}
        if name not in new_tests:
            out.append(Finding("TT01", "high", path, fn.lineno, f"`{name}` was deleted", data))
            continue
        nf = new_tests[name]
        a_old, a_new = _assert_nodes(fn), _assert_nodes(nf)
        if len(a_new) < len(a_old):
            out.append(Finding("TT02", "high", path, nf.lineno,
                               f"`{name}` went from {len(a_old)} to {len(a_new)} assertions", data))
        if _is_skipped(nf) and not _is_skipped(fn):
            out.append(Finding("TT03", "high", path, nf.lineno, f"`{name}` is now skipped/xfail", data))

        old_trivial = sum(_is_trivial(a) for a in a_old)
        for a in a_new:
            if _is_trivial(a) and old_trivial == 0:
                out.append(Finding("TT04", "high", path, a.lineno,
                                   f"`{ast.unparse(a)}` in `{name}` can never fail", data))

        old_parts = {p[0]: p[1] for a in a_old if (p := _compare_parts(a) or _call_parts(a, before))}
        for a in a_new:
            p = _compare_parts(a) or _call_parts(a, after)
            if p and p[0] in old_parts and old_parts[p[0]] != p[1]:
                if "approx(" in p[1] and "approx(" in old_parts[p[0]]:
                    continue  # tolerance-only change, reported once as TT06
                out.append(Finding("TT05", "medium", path, a.lineno,
                                   f"`{p[0]}` expectation changed: `{old_parts[p[0]]}` -> `{p[1]}`", data))

        t_old, t_new = _max_tolerance(fn), _max_tolerance(nf)
        if t_old and t_new > t_old:
            out.append(Finding("TT06", "medium", path, nf.lineno,
                               f"`{name}` tolerance loosened {t_old:g} -> {t_new:g} "
                               f"({t_new / t_old:.0f}x)", data))
    return out


# --------------------------------------------------------------------------- #
# Source-side detectors
# --------------------------------------------------------------------------- #
def test_literals(test_sources: dict[str, str]) -> set:
    """Distinctive constants used in tests (ignores 0, 1, 2, True, None, short strings)."""
    lits = set()
    for src in test_sources.values():
        tree = _parse(src)
        if tree is None:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and not isinstance(n.value, bool) and n.value is not None:
                v = n.value
                if isinstance(v, (int, float)) and abs(v) > 2:
                    lits.add(v)
                elif isinstance(v, str) and len(v) >= 3:
                    lits.add(v)
    return lits


def _branch_tests(tree: ast.Module | None) -> set[str]:
    if tree is None:
        return set()
    return {ast.unparse(n.test) for n in ast.walk(tree) if isinstance(n, (ast.If, ast.IfExp))}


def _swallowing_handlers(tree: ast.Module | None) -> list[tuple[ast.Try, ast.ExceptHandler]]:
    """(try, handler) pairs for broad handlers that drop the error silently.

    The enclosing `try` is returned too: the SC02 proof experiments edit both.
    """
    out = []
    if tree is None:
        return out
    for t in ast.walk(tree):
        if not isinstance(t, (ast.Try, ast.TryStar)):
            continue
        for n in t.handlers:
            broad = n.type is None or ast.unparse(n.type) in ("Exception", "BaseException")
            quiet = all(isinstance(s, (ast.Pass, ast.Continue)) or
                        (isinstance(s, ast.Return) and (s.value is None or isinstance(s.value, ast.Constant)))
                        for s in n.body)
            if broad and quiet:
                out.append((t, n))
    return out


RANGE_OPS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def _near_test_values(v, lits: set) -> list:
    """Numeric test values that a range bound `v` sits on or right next to.

    `99 < qty < 101` never names 100, but brackets it; an int bound one away
    from a test value counts. Static severity stays low because a legitimate
    tier (`qty >= 100`) matches too; the spike probe tells them apart.
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return []
    out = []
    for t in lits:
        if isinstance(t, bool) or not isinstance(t, (int, float)):
            continue
        if v == t or (isinstance(v, int) and isinstance(t, int) and abs(v - t) == 1):
            out.append(t)
    return out


SUPPRESS = re.compile(r"#\s*(noqa|type:\s*ignore|pragma:\s*no\s*cover|pylint:\s*disable)")


def diff_source(path: str, before: str | None, after: str | None, lits: set) -> list[Finding]:
    t_old, t_new = _parse(before), _parse(after)
    out: list[Finding] = []
    if t_new is None:
        return out

    old_branches = _branch_tests(t_old)
    for n in ast.walk(t_new):
        if not isinstance(n, ast.If):
            continue
        cond = ast.unparse(n.test)
        if cond in old_branches:
            continue
        eq_hits: list = []
        range_hits: list = []
        for c in ast.walk(n.test):
            if not isinstance(c, ast.Compare):
                continue
            consts = [x.value for x in [c.left, *c.comparators] if isinstance(x, ast.Constant)]
            if any(isinstance(op, (ast.Eq, ast.In, ast.Is)) for op in c.ops):
                eq_hits += [v for v in consts if v in lits]
            if any(isinstance(op, RANGE_OPS) for op in c.ops):
                range_hits += [t for v in consts for t in _near_test_values(v, lits)]
        if eq_hits:
            out.append(Finding("SC01", "high", path, n.lineno,
                               f"new branch `if {cond}:` matches test value(s) {eq_hits}",
                               {"line": n.lineno, "literals": eq_hits}))
        elif range_hits:
            hits = sorted(set(range_hits))
            out.append(Finding("SC01", "low", path, n.lineno,
                               f"new range branch `if {cond}:` brackets test value(s) {hits}",
                               {"line": n.lineno, "literals": hits, "kind": "range"}))

    old_h = {ast.unparse(h) for _, h in _swallowing_handlers(t_old)}
    for t, h in _swallowing_handlers(t_new):
        if ast.unparse(h) not in old_h:
            out.append(Finding("SC02", "medium", path, h.lineno,
                               f"`{ast.unparse(h).splitlines()[0]}` hides errors",
                               {"line": h.lineno, "try_line": t.lineno}))

    old_count = len(SUPPRESS.findall(before or ""))
    new_lines = [(i, ln) for i, ln in enumerate((after or "").splitlines(), 1) if SUPPRESS.search(ln)]
    if len(new_lines) > old_count:
        for i, ln in new_lines[old_count:]:
            out.append(Finding("SC03", "low", path, i, f"suppression added: `{ln.strip()}`"))

    f_old, f_new = functions(t_old), functions(t_new)
    for name, fn in f_new.items():
        if name.rsplit(".", 1)[-1].startswith("_") or name not in f_old:
            continue
        if ast.unparse(fn.args) != ast.unparse(f_old[name].args):
            out.append(Finding("API01", "medium", path, fn.lineno,
                               f"`{name}({ast.unparse(f_old[name].args)})` -> `({ast.unparse(fn.args)})`"))
    return out


def changed_functions(before: str | None, after: str | None) -> dict[str, ast.FunctionDef]:
    f_old, f_new = functions(_parse(before)), functions(_parse(after))
    return {k: v for k, v in f_new.items()
            if k not in f_old or ast.unparse(v) != ast.unparse(f_old[k])}


def call_counts(all_sources: dict[str, str]) -> Counter:
    """How often each name is called across the non-test sources. Parse the repo once per
    audit, not once per changed file."""
    counts: Counter = Counter()
    for p, s in all_sources.items():
        tree = None if is_test_file(p) else _parse(s)
        if tree is None:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                counts[f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)] += 1
    return counts


def coverage_and_blast(path: str, changed: dict[str, ast.FunctionDef],
                       test_text: str, calls: Counter) -> list[Finding]:
    out = []
    for qual, fn in changed.items():
        short = qual.rsplit(".", 1)[-1]
        if short.startswith("_"):
            continue
        if not re.search(rf"\b{re.escape(short)}\b", test_text):
            out.append(Finding("COV01", "medium", path, fn.lineno, f"`{qual}` changed but no test calls it"))
        callers = calls[short]
        if callers >= 5:
            out.append(Finding("BR01", "low", path, fn.lineno, f"`{qual}` has {callers} call sites"))
        elif callers:
            out.append(Finding("BR01", "info", path, fn.lineno, f"`{qual}` has {callers} call site(s)"))
    return out
