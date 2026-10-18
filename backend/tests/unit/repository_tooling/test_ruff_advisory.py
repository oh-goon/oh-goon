from __future__ import annotations

from oh_goon.repository_tooling.ruff_advisory import render_ruff_advisory


def _finding(*, code: str, fix: str | None, row: int = 3) -> dict[str, object]:
    payload: dict[str, object] = {
        "filename": "src/example.py",
        "code": code,
        "message": f"message for {code}",
        "location": {"row": row, "column": 7},
        "fix": None,
    }
    if fix is not None:
        payload["fix"] = {
            "applicability": fix,
            "message": "repair",
            "edits": [],
        }
    return payload


def test_ruff_advisory_hides_safe_autofix_noise_and_keeps_review_findings() -> None:
    report = render_ruff_advisory(
        [
            _finding(code="F401", fix="safe"),
            _finding(code="F841", fix="safe"),
            _finding(code="F821", fix=None, row=11),
            _finding(code="F601", fix="unsafe", row=12),
        ]
    )

    assert "Findings: 4" in report
    assert "Safe auto-fix available: 2 (run: make ruff-repair)" in report
    assert "Review-required findings: 2" in report
    assert "F401" not in report
    assert "F841" not in report
    assert "src/example.py:11:7: F821 message for F821" in report
    assert "src/example.py:12:7: F601 message for F601 [unsafe fix available]" in report


def test_ruff_advisory_does_not_claim_unsafe_fix_is_safe() -> None:
    report = render_ruff_advisory([_finding(code="F601", fix="unsafe")])
    assert "Safe auto-fix available: 0" in report
    assert "Review-required findings: 1" in report
    assert "[unsafe fix available]" in report
