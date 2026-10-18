from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Mapping
from typing import Any, TextIO


def _safe_fixable(finding: Mapping[str, Any]) -> bool:
    fix = finding.get("fix")
    return isinstance(fix, Mapping) and fix.get("applicability") == "safe"


def _location(finding: Mapping[str, Any]) -> tuple[int, int]:
    location = finding.get("location")
    if not isinstance(location, Mapping):
        return (1, 1)
    row = location.get("row")
    column = location.get("column")
    return (
        row if isinstance(row, int) else 1,
        column if isinstance(column, int) else 1,
    )


def _render_review_finding(finding: Mapping[str, Any]) -> str:
    filename = finding.get("filename")
    code = finding.get("code")
    message = finding.get("message")
    row, column = _location(finding)
    fix = finding.get("fix")
    suffix = ""
    if isinstance(fix, Mapping) and fix.get("applicability") == "unsafe":
        suffix = " [unsafe fix available]"
    return f"{filename}:{row}:{column}: {code} {message}{suffix}"


def render_ruff_advisory(findings: Iterable[Mapping[str, Any]]) -> str:
    materialized = list(findings)
    safe = [finding for finding in materialized if _safe_fixable(finding)]
    review = [finding for finding in materialized if not _safe_fixable(finding)]
    lines = [
        "RUFF ADVISORY",
        f"Findings: {len(materialized)}",
        f"Safe auto-fix available: {len(safe)} (run: make ruff-repair)",
        f"Review-required findings: {len(review)}",
    ]
    lines.extend(_render_review_finding(finding) for finding in review)
    return "\n".join(lines)


def _load_findings(stream: TextIO) -> list[Mapping[str, Any]]:
    payload = json.load(stream)
    if not isinstance(payload, list) or not all(isinstance(item, Mapping) for item in payload):
        raise ValueError("Ruff JSON output must be a list of finding objects")
    return payload


def main() -> int:
    try:
        findings = _load_findings(sys.stdin)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"RUFF ADVISORY REPORT UNAVAILABLE: {exc}", file=sys.stderr)
        return 2
    print(render_ruff_advisory(findings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
