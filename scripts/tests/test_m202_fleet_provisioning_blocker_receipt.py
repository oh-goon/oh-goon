"""M202: native WSL proved the wheel and full development ladder; missing enrollment stays blocked."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oh_goon import workflow
from oh_goon.core.enforcer_lace_trust import (
    UNPROVISIONED_FLEET_ADMISSION,
    UNPROVISIONED_FLEET_PLAN_INGRESS,
)
from oh_goon.repository_tooling import certification_bootstrap_protocol as protocol


def _run_blocked(monkeypatch, capsys, tail: str) -> tuple[int, str]:
    monkeypatch.setattr(
        workflow,
        "_certification_environment",
        lambda: ("repo", {}, Path("/state/locks")),
    )
    monkeypatch.setattr(workflow, "_run_plan", lambda **_: (2, tail))
    rc = workflow._run_self_plan("certify")
    return rc, capsys.readouterr().out


@pytest.mark.parametrize(
    "reason", [UNPROVISIONED_FLEET_PLAN_INGRESS, UNPROVISIONED_FLEET_ADMISSION]
)
def test_original_native_denial_yields_one_non_authorizing_receipt(
    monkeypatch, capsys, reason: str
) -> None:
    rc, output = _run_blocked(monkeypatch, capsys, reason + "\n")
    assert rc == 2
    assert "BLOCKED BY FLEET ENFORCER PROVISIONING" in output
    lines = [
        line
        for line in output.splitlines()
        if line.startswith(protocol.FLEET_PROVISIONING_BLOCKER_RECORD_ID + "=")
    ]
    assert len(lines) == 1
    receipt = json.loads(lines[0].split("=", 1)[1])
    assert receipt == {
        "schema": "oh-goon.fleet-provisioning-blocker/v1",
        "reason": "FLEET_ENFORCER_NOT_PROVISIONED",
        "status": "BLOCKED",
        "authority": "NOT_ESTABLISHED",
        "certification_goon_started": False,
        "app_pass": False,
        "authenticated_replay": "NOT_AUTHORIZED",
        "group_a_gate_a": "NOT_RUN",
    }
    assert "APP certification passed" not in output


@pytest.mark.parametrize(
    "tail",
    [
        "unrelated error: " + UNPROVISIONED_FLEET_PLAN_INGRESS + "\n",
        "diagnostic quotes '"
        + UNPROVISIONED_FLEET_ADMISSION
        + "' but failed due to lock drift\n",
        "unknown missing authority\n",
    ],
)
def test_unrelated_failure_cannot_forge_provisioning_classification(
    monkeypatch, capsys, tail: str
) -> None:
    rc, output = _run_blocked(monkeypatch, capsys, tail)
    assert rc == 2
    assert "APPLICATION FAILED" in output
    assert protocol.FLEET_PROVISIONING_BLOCKER_RECORD_ID not in output


def test_readonly_classifier_does_not_fall_back_to_substring_match() -> None:
    assert protocol.is_missing_fleet_provisioning(
        "error\n" + UNPROVISIONED_FLEET_PLAN_INGRESS + "\n"
    )
    assert not protocol.is_missing_fleet_provisioning(
        "other: " + UNPROVISIONED_FLEET_PLAN_INGRESS
    )
    assert not protocol.is_missing_fleet_provisioning("")
