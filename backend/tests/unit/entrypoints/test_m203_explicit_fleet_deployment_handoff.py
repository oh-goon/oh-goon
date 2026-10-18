"""M203: deployment transports one original provisioner, never CLI data or ambient state."""

from __future__ import annotations

import pytest

from oh_goon import cli
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from tests.unit.services.test_group_a_fleet_provisioning_authority import provisioned

PLAN_ARGS = [
    "plan",
    "run",
    "nonexistent-plan.yml",
    "--repository-id",
    "repo-1",
    "--fleet-run-id",
    "durable-run",
]


@pytest.fixture
def deployment_provisioner(tmp_path, monkeypatch):
    _, _, connection, _, _, _, provisioner, *_ = provisioned(tmp_path, monkeypatch)
    try:
        yield provisioner
    finally:
        connection.close()


def _guard_before_work(monkeypatch):
    """Make any pre-command work or DB/runtime construction visibly forbidden."""
    monkeypatch.setattr(
        cli, "_run_pre_runtime_command", lambda _args: pytest.fail("pre-runtime work began")
    )
    monkeypatch.setattr(
        cli, "_build_application_runtime", lambda **_kw: pytest.fail("runtime work began")
    )


def test_original_deployment_object_transported_once_to_public_composition(
    monkeypatch, deployment_provisioner
):
    provisioner = deployment_provisioner
    events = []
    runtime = object()

    def builder(cls, *, fleet_provisioner=None):
        events.append(("build", fleet_provisioner))
        return runtime

    monkeypatch.setattr(cli.ApplicationRuntime, "build", classmethod(builder))
    monkeypatch.setattr(cli, "_run_pre_runtime_command", lambda _args: None)
    monkeypatch.setattr(
        cli,
        "_execute_stateful_command",
        lambda built, args: (events.append(("command", built, args.fleet_run_id)) or 0),
    )
    assert cli.main(PLAN_ARGS, fleet_provisioner=provisioner) == 0
    assert events == [("build", provisioner), ("command", runtime, "durable-run")]


@pytest.mark.parametrize("run_id", ["not-enrolled", "", None])
def test_wrong_run_enrollment_blocks_before_any_runtime_or_plan_read(
    monkeypatch, run_id, deployment_provisioner
):
    provisioner = deployment_provisioner
    _guard_before_work(monkeypatch)
    args = ["plan", "run", "missing.yml", "--repository-id", "repo-1"]
    if run_id is not None:
        args.extend(["--fleet-run-id", run_id])
    with pytest.raises(TrustProtocolError, match="(not explicitly provisioned|identity required)"):
        cli.main(args, fleet_provisioner=provisioner)


@pytest.mark.parametrize(
    "args",
    [
        ["serve"],
        ["start"],
        ["self", "certify", "--plan"],
        ["plan", "validate", "missing.yml"],
        ["plan", "prepare", "missing.yml"],
        ["plan", "preview", "missing.yml", "repo-1"],
    ],
)
def test_deployment_handoff_cannot_be_ignored_or_leak_to_other_commands(
    monkeypatch, args, deployment_provisioner
):
    provisioner = deployment_provisioner
    _guard_before_work(monkeypatch)
    with pytest.raises(TrustProtocolError, match="only valid for plan run"):
        cli.main(args, fleet_provisioner=provisioner)


@pytest.mark.parametrize("invalid", [object(), (), {"fleet_run_id": "durable-run"}, "env:ISSUER"])
def test_forged_handoff_rejected_before_any_work(monkeypatch, invalid):
    _guard_before_work(monkeypatch)
    with pytest.raises(TrustProtocolError, match="original deployment"):
        cli.main(PLAN_ARGS, fleet_provisioner=invalid)


def test_ordinary_shell_cli_cannot_read_environment_as_provisioner(monkeypatch):
    monkeypatch.setenv("OH_GOON_FLEET_PROVISIONER", "make-me-a-signer")
    monkeypatch.setenv("OH_GOON_FLEET_ISSUER_KEYS", "/tmp/pretend")
    observed = []
    monkeypatch.setattr(cli, "_run_pre_runtime_command", lambda _args: None)
    monkeypatch.setattr(
        cli.ApplicationRuntime,
        "build",
        classmethod(
            lambda cls, *, standard_local=False: observed.append(
                "standard-local" if standard_local else "no-provisioner"
            )
            or object()
        ),
    )
    monkeypatch.setattr(cli, "_execute_stateful_command", lambda _runtime, _args: 2)
    assert cli.main(PLAN_ARGS) == 2
    assert observed == ["standard-local"]
