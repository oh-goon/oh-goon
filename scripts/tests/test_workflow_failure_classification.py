from __future__ import annotations

from pathlib import Path

import oh_goon.workflow as workflow_run


def test_admitted_pytest_failure_outranks_environment_like_child_output(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(workflow_run, "ROOT", tmp_path)
    monkeypatch.setattr(
        workflow_run, "_certification_product_version", lambda: "1267.0.878"
    )
    monkeypatch.setattr(
        workflow_run,
        "_certification_environment",
        lambda: ("repo-test", {}, Path("/state/locks")),
    )
    monkeypatch.setattr(
        workflow_run,
        "_run_plan",
        lambda **_kwargs: (
            1,
            "CERTIFICATION PLAN FAILED\n"
            "=========================== FAILURES ===========================\n"
            "FAILED scripts/tests/test_run_logging.py::test_logged_runner\n"
            "host system is missing dependencies\n",
        ),
    )

    assert workflow_run.run_app() == 1
    output = capsys.readouterr().out
    assert "APPLICATION FAILED" in output
    assert "BLOCKED BY ENVIRONMENT" not in output
