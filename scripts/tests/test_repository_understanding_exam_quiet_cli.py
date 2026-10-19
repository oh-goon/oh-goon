from __future__ import annotations

import pytest

import argparse

from oh_goon.repository_tooling import _repository_understanding_exam_cli as cli


def _payload() -> dict[str, object]:
    return {
        "source_version": "candidate",
        "question_count": 219,
        "qualification_question_count": 157,
        "critical_question_count": 45,
        "detail": {"large": ["diagnostic"] * 20},
    }


def test_verify_quiet_emits_concise_pass_without_full_audit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = argparse.Namespace(command="verify", manifest=None, quiet=True)

    assert cli._verify(args, {"verify": lambda **_kwargs: _payload()}) == 0

    output = capsys.readouterr().out
    assert output == (
        "REPOSITORY UNDERSTANDING EXAM: PASS "
        "(source=candidate, questions=219, qualification=157, critical=45)\n"
    )
    assert "diagnostic" not in output


def test_verify_default_retains_full_diagnostic_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = argparse.Namespace(command="verify", manifest=None, quiet=False)

    assert cli._verify(args, {"verify": lambda **_kwargs: _payload()}) == 0

    output = capsys.readouterr().out
    assert '"source_version": "candidate"' in output
    assert '"detail"' in output
