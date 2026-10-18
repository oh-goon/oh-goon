"""M197: certification must report missing original-owner Fleet authority honestly."""
from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon import workflow as workflow_run
from oh_goon.core.enforcer_lace_trust import (
    UNPROVISIONED_FLEET_ADMISSION,
    UNPROVISIONED_FLEET_PLAN_INGRESS,
)


@pytest.mark.parametrize("denial", [
    UNPROVISIONED_FLEET_PLAN_INGRESS,
    UNPROVISIONED_FLEET_ADMISSION,
])
def test_unprovisioned_native_fleet_is_a_blocked_authority_not_application_bug(
    denial: str, monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(
        workflow_run, "_certification_environment",
        lambda: ("repo-verified", {}, Path("/state/locks")),
    )
    calls = []

    def fail_plan(**kwargs):
        calls.append(kwargs)
        return 2, denial + "\n"

    monkeypatch.setattr(workflow_run, "_run_plan", fail_plan)
    assert workflow_run._run_self_plan("certify") == 2
    assert len(calls) == 1
    output = capsys.readouterr().out
    assert "BLOCKED BY FLEET ENFORCER PROVISIONING" in output
    assert "FleetPreworkProvisioner" in output
    assert "No default signing keys or fallback issuer will be created" in output
    assert "No certification Goon ran" in output
    assert "APP PASS and release remain blocked" in output
    assert "APPLICATION FAILED" not in output
    assert "APP CERTIFICATION PASSED" not in output


def test_other_prework_failures_are_not_silently_classified_as_missing_provisioning(
    monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(
        workflow_run, "_certification_environment",
        lambda: ("repo-verified", {}, Path("/state/locks")),
    )
    monkeypatch.setattr(
        workflow_run, "_run_plan", lambda **_: (2, "invalid canonical Plan source\n"),
    )
    assert workflow_run._run_self_plan("certify") == 2
    output = capsys.readouterr().out
    assert "APPLICATION FAILED" in output
    assert "BLOCKED BY FLEET ENFORCER PROVISIONING" not in output


def test_admitted_run_error_does_not_reclassify_as_missing_prework_enrollment(
    monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(
        workflow_run, "_certification_environment",
        lambda: ("repo-verified", {}, Path("/state/locks")),
    )
    monkeypatch.setattr(
        workflow_run, "_run_plan", lambda **_: (
            2,
            "Fleet Run ID: already-admitted\n" + UNPROVISIONED_FLEET_ADMISSION + "\n",
        ),
    )
    assert workflow_run._run_self_plan("certify") == 2
    output = capsys.readouterr().out
    assert "BLOCKED BY FLEET ENFORCER PROVISIONING" not in output
    assert "APP CERTIFICATION PASSED" not in output
