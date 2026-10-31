"""Consume native Vitest results without deduplicating assertion occurrences."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def vitest_outcomes(report: Mapping[str, Any]) -> tuple[bool, bool, dict[str, int]]:
    """Return coverage and all-PASS facts from one native configured invocation."""
    required = (
        "numTotalTests",
        "numPassedTests",
        "numFailedTests",
        "numPendingTests",
        "numTodoTests",
        "numTotalTestSuites",
        "numPassedTestSuites",
        "numFailedTestSuites",
        "numPendingTestSuites",
    )
    if any(type(report.get(name)) is not int or report[name] < 0 for name in required):
        raise ValueError("native Vitest outcome counts are invalid or missing")
    counts = {name: int(report[name]) for name in required}
    results = report.get("testResults")
    if (
        not isinstance(results, list)
        or not results
        or any(
            not isinstance(row, dict) or not isinstance(row.get("assertionResults"), list)
            for row in results
        )
    ):
        raise ValueError("native Vitest assertion results are missing")
    # Different native projects can report the same path/title. Every occurrence
    # remains an instance; the configured invocation supplies their common variant.
    assertions = [item for row in results for item in row["assertionResults"]]
    if any(
        not isinstance(item, dict)
        or item.get("status") not in {"passed", "failed", "pending", "todo", "skipped"}
        for item in assertions
    ):
        raise ValueError("native Vitest assertion outcome is unknown")
    complete = (
        counts["numTotalTests"] > 0
        and len(assertions) == counts["numTotalTests"]
        and report.get("wasInterrupted", False) is False
        and report.get("numRuntimeErrorTestSuites", 0) == 0
        and counts["numPendingTestSuites"] == 0
    )
    satisfied = (
        complete
        and report.get("success") is True
        and counts["numPassedTests"] == counts["numTotalTests"]
        and counts["numTotalTestSuites"] == counts["numPassedTestSuites"]
        and all(item["status"] == "passed" for item in assertions)
        and not any(
            counts[name]
            for name in ("numFailedTests", "numPendingTests", "numTodoTests", "numFailedTestSuites")
        )
    )
    return complete, satisfied, counts
