"""Matching a finding's test to JUnit result ids must be exact.

A loose match lets one test's outcome prove or disprove a finding about a
different test, and missing parametrized ids leaves findings unproven.
Result ids have the form `<classname>.<name>`, as built by `runners.parse_junit_xml`.
"""

from __future__ import annotations

from tamperproof.proof import _match


def test_suffix_without_dot_boundary_does_not_match():
    results = {"test_m.my_test_a": "failed", "test_m.test_a": "passed"}
    assert _match(results, "test_a", "test_m.py") == {"test_m.test_a": "passed"}


def test_parametrized_cases_all_match():
    results = {"test_m.test_x[1]": "passed", "test_m.test_x[2]": "failed",
               "test_m.test_xy[1]": "failed"}
    assert set(_match(results, "test_x", "test_m.py")) == {"test_m.test_x[1]", "test_m.test_x[2]"}


def test_same_name_in_other_file_does_not_match():
    results = {"tests.test_one.test_a": "passed", "tests.test_two.test_a": "failed"}
    assert _match(results, "test_a", "tests/test_one.py") == {"tests.test_one.test_a": "passed"}


def test_class_is_part_of_the_match():
    results = {"test_m.TestX.test_a": "passed", "test_m.TestY.test_a": "failed",
               "test_m.test_a": "failed"}
    assert _match(results, "TestX.test_a", "test_m.py") == {"test_m.TestX.test_a": "passed"}


def test_module_level_test_does_not_match_class_method():
    results = {"test_m.TestX.test_a": "failed", "test_m.test_a": "passed"}
    assert _match(results, "test_a", "test_m.py") == {"test_m.test_a": "passed"}


def test_rootdir_prefix_differences_still_match():
    # pytest may report `tests.test_one` or just `test_one`, depending on rootdir.
    results = {"test_one.test_a": "failed"}
    assert _match(results, "test_a", "tests/test_one.py") == {"test_one.test_a": "failed"}
