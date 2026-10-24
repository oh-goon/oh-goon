"""M179: CLI Plan ingress uses the original lifecycle before Plan resolution.

A missing enrollment is rejected without parsing Plan inputs, visiting source,
observing issuer trust, creating journal state, or invoking the planner.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime_plans import run_plan_command
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.vocabulary import FleetRunStatus
from oh_goon.services.fleet_plans import FleetPlanService
from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle


def args(*, run_id: str | None = None, inputs=None) -> Namespace:
    parsed = build_parser().parse_args(
        [
            "dev",
            "_run-plan",
            "missing-plan.yml",
            "--repository-id",
            "repo",
            "--strict",
            "--no-wait",
            "--lock-file",
            "goon.lock",
        ]
    )
    parsed.command, parsed.plan_command = "plan", "run"
    parsed.fleet_run_id = run_id
    parsed._selected_inputs = dict(item.split("=", 1) for item in inputs or () if "=" in item)
    parsed._caller_input_origins = dict.fromkeys(parsed._selected_inputs, "caller_file")
    return parsed


def lifecycle(*, provisioner: FleetPreworkProvisioner | None, authenticated=True):
    # No database or native host effects are needed to exercise the owner.
    owner = object.__new__(FleetRunLifecycle)
    owner._require_authenticated_fleet = authenticated
    owner._standard_local_fleet = False
    owner._fleet_provisioner = provisioner
    return owner


def provisioner(run_ids: tuple[str, ...]) -> FleetPreworkProvisioner:
    # Constructor's native trust/inventory validation is covered elsewhere;
    # this synthetic enrolled inventory isolates the pre-observation identity gate.
    authority = object.__new__(FleetPreworkProvisioner)
    object.__setattr__(
        authority, "enrolled", tuple(SimpleNamespace(run_id=run_id) for run_id in run_ids)
    )
    return authority


def plans(owner) -> FleetPlanService:
    control = object.__new__(FleetRunControlPlane)
    control._lifecycle = owner
    return FleetPlanService(object(), control, object(), object(), object())


@pytest.mark.parametrize("run_id", (None, "", "known", "forged", 7))
def test_no_provisioner_denies_cli_before_plan_source_and_any_effect(run_id, capsys):
    service = plans(lifecycle(provisioner=None))

    def forbidden(*args, **kwargs):
        pytest.fail("CLI must deny before Plan reading, parsing, or admission")

    service.run = forbidden
    runtime = SimpleNamespace(plans=service)
    assert run_plan_command(runtime, args(run_id=run_id, inputs=["not-an-assignment"])) == 2
    assert "not provisioned" in capsys.readouterr().out


@pytest.mark.parametrize("run_id", (None, "", "wrong-id", 1, True))
def test_enrollment_selection_denies_missing_or_forged_cli_identity_before_plan(run_id, capsys):
    service = plans(lifecycle(provisioner=provisioner(("known",))))
    service.run = lambda *a, **kw: pytest.fail("must not resolve Plan or reach Fleet")
    assert run_plan_command(SimpleNamespace(plans=service), args(run_id=run_id)) == 2
    assert "required" in capsys.readouterr().out or run_id == "wrong-id"


def test_enrollment_selection_does_not_observe_or_sign_before_plan(monkeypatch):
    enrolled = provisioner(("known",))
    monkeypatch.setattr(
        FleetPreworkProvisioner,
        "_observe",
        lambda *a, **kw: pytest.fail("no native issuer observation at CLI identity fence"),
    )
    plans(lifecycle(provisioner=enrolled)).require_cli_plan_preparation("known")
    with pytest.raises(TrustProtocolError, match="not explicitly provisioned"):
        enrolled.require_enrolled_identity("unknown")


def test_hardened_cli_accepts_only_exact_enrolled_identity_then_delegates_one_plan_run(capsys):
    service = plans(lifecycle(provisioner=provisioner(("known",))))
    prepared = []
    admitted = []
    target = SimpleNamespace(
        source=PlanFileSource(sha256="a" * 64), plan=SimpleNamespace(plan_id="plan-A")
    )

    def prepare(plan_path, **kwargs):
        prepared.append((plan_path, kwargs))
        return target

    def start(value, **kwargs):
        admitted.append((value, kwargs))
        return SimpleNamespace(fleet_run_id="known", target=target, status=FleetRunStatus.QUEUED)

    service.prepare_execution_target = prepare
    service._runs.start_prepared_plan = start
    code = run_plan_command(
        SimpleNamespace(plans=service), args(run_id="known", inputs=["ENV=test"])
    )
    assert code == 0
    assert prepared == [
        (
            Path("missing-plan.yml"),
            {
                "extensions_path": None,
                "inputs": {"ENV": "test"},
                "source": None,
                "expected_plan_id": None,
                "root_expected_name": None,
                "lock_path": Path("goon.lock"),
                "caller_input_file": None,
                "caller_input_origins": {"ENV": "caller_file"},
                "external_artifacts": (),
                "repository_goon": False,
                "source_graph": None,
                "repository": None,
            },
        )
    ]
    assert len(admitted) == 1 and admitted[0][0] is target
    assert admitted[0][1]["fleet_run_id"] == "known"
    assert admitted[0][1]["repository_ids"] == {"repo"}
    assert "Fleet Run ID: known" in capsys.readouterr().out


def test_cli_gate_is_pass_through_in_explicit_nonproduction_lifecycle():
    service = plans(lifecycle(provisioner=None, authenticated=False))
    service.require_cli_plan_preparation(None)


def test_cli_gate_never_authorizes_final_issuer_observation(monkeypatch):
    enrolled = provisioner(("known",))
    owner = lifecycle(provisioner=enrolled)
    service = plans(owner)
    service.require_cli_plan_preparation("known")
    monkeypatch.setattr(
        FleetPreworkProvisioner,
        "_observe",
        lambda *a, **kw: (_ for _ in ()).throw(TrustProtocolError("signer revoked")),
    )
    with pytest.raises(TrustProtocolError, match="signer revoked"):
        enrolled.resolve("known")
