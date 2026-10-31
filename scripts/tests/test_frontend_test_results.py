from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from oh_goon.repository_tooling.frontend_test_results import vitest_outcomes


def _native() -> dict[str, Any]:
    return {
        "numTotalTests": 2,
        "numPassedTests": 2,
        "numFailedTests": 0,
        "numPendingTests": 0,
        "numTodoTests": 0,
        "numTotalTestSuites": 2,
        "numPassedTestSuites": 2,
        "numFailedTestSuites": 0,
        "numPendingTestSuites": 0,
        "success": True,
        "testResults": [
            {
                "name": "same.test.ts",
                "status": "passed",
                "assertionResults": [{"fullName": "same title", "status": "passed"}],
            },
            {
                "name": "same.test.ts",
                "status": "passed",
                "assertionResults": [{"fullName": "same title", "status": "passed"}],
            },
        ],
    }


def test_native_assertion_occurrences_are_preserved_without_title_deduplication() -> (
    None
):
    complete, satisfied, counts = vitest_outcomes(_native())
    assert complete and satisfied
    assert counts["numTotalTests"] == counts["numPassedTests"] == 2


@pytest.mark.parametrize("status", ("failed", "pending", "todo", "skipped"))
def test_native_non_pass_outcome_cannot_hide_behind_green_counters(status: str) -> None:
    report = _native()
    assert vitest_outcomes(report)[1] is True
    report["testResults"][0]["assertionResults"][0]["status"] = status
    assert vitest_outcomes(report)[1] is False


@pytest.mark.parametrize(
    "damage",
    (
        "zero",
        "omission",
        "interrupt",
        "runtime-error",
        "pending-suite",
        "failed-suite",
        "failed-native-success",
    ),
)
def test_missing_or_unsatisfied_native_work_is_nonqualifying(damage: str) -> None:
    report = _native()
    assert vitest_outcomes(report)[1] is True
    if damage == "zero":
        report["numTotalTests"] = report["numPassedTests"] = 0
        report["testResults"] = []
    elif damage == "omission":
        report["testResults"].pop()
    elif damage == "interrupt":
        report["wasInterrupted"] = True
    elif damage == "runtime-error":
        report["numRuntimeErrorTestSuites"] = 1
    elif damage == "pending-suite":
        report["numPendingTestSuites"] = 1
    elif damage == "failed-suite":
        report["numFailedTestSuites"] = 1
    else:
        report["success"] = False
    if damage == "zero":
        with pytest.raises(ValueError, match="assertion results"):
            vitest_outcomes(report)
    else:
        assert vitest_outcomes(report)[1] is False


def test_unknown_or_malformed_native_result_is_rejected() -> None:
    report = _native()
    assert vitest_outcomes(report)[1] is True
    for damage in ("unknown", "missing", "boolean"):
        changed = deepcopy(report)
        if damage == "unknown":
            changed["testResults"][0]["assertionResults"][0]["status"] = (
                "some-future-status"
            )
        elif damage == "missing":
            changed.pop("numTotalTests")
        else:
            changed["numPassedTests"] = True
        with pytest.raises(ValueError):
            vitest_outcomes(changed)
