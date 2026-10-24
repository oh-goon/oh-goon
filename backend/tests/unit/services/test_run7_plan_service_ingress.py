"""RUN-7: service Run ingress must gate before real Plan work.

This exercises FleetPlanService -> FleetRunControlPlane -> FleetRunLifecycle,
not a new admission implementation. It does not certify native physical custody.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.vocabulary import FleetRunSource, FleetRunStatus
from oh_goon.services.fleet_plans import FleetPlanService
from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle


class _ForbiddenResolver:
    def __getattr__(self, name):
        raise AssertionError(f"No Plan resolution should occur: {name}")


def _real_owner(*, provisioned_ids: tuple[str, ...] | None, authenticated: bool = True):
    lifecycle = object.__new__(FleetRunLifecycle)
    lifecycle._require_authenticated_fleet = authenticated
    if provisioned_ids is None:
        lifecycle._fleet_provisioner = None
    else:
        provisioner = object.__new__(FleetPreworkProvisioner)
        object.__setattr__(
            provisioner,
            "enrolled",
            tuple(SimpleNamespace(run_id=run_id) for run_id in provisioned_ids),
        )
        lifecycle._fleet_provisioner = provisioner
    control = object.__new__(FleetRunControlPlane)
    control._lifecycle = lifecycle
    return FleetPlanService(_ForbiddenResolver(), control, object(), object(), object()), control


@pytest.mark.parametrize("reviewed", [False, True])
@pytest.mark.parametrize(
    "provisioned_ids,run_id",
    [
        (None, "known"),
        ((), "known"),
        (("known",), None),
        (("known",), "foreign"),
    ],
)
def test_original_lifecycle_rejects_service_run_before_plan_resolution(
    tmp_path: Path,
    reviewed: bool,
    provisioned_ids,
    run_id,
):
    plans, control = _real_owner(provisioned_ids=provisioned_ids)
    control.start_prepared_plan = lambda *a, **kw: pytest.fail("no admission before identity")
    path = tmp_path / "do-not-read.yml"
    path.write_text("INVALID: [", encoding="utf-8")
    with pytest.raises(TrustProtocolError):
        if reviewed:
            plans.run_reviewed(path, expected_execution_digest="bad", fleet_run_id=run_id)
        else:
            plans.run(path, fleet_run_id=run_id)
    # The attempted YAML is intentionally malformed and must be untouched.
    assert path.read_text(encoding="utf-8") == "INVALID: ["


@pytest.mark.parametrize("reviewed", [False, True])
def test_exact_enrolled_service_run_reaches_one_existing_preparation_and_admission(
    reviewed: bool,
    monkeypatch: pytest.MonkeyPatch,
):
    plans, control = _real_owner(provisioned_ids=("known",))
    target = SimpleNamespace(
        source=PlanFileSource(sha256="a" * 64),
        plan=SimpleNamespace(plan_id="original-plan"),
    )
    calls = []

    def prepared(path, **kwargs):
        calls.append(("prepare", path, kwargs))
        return target

    def admitted(received_target, **kwargs):
        assert received_target is target
        calls.append(("admit", kwargs["fleet_run_id"], kwargs["source"]))
        return SimpleNamespace(fleet_run_id="known", target=target, status=FleetRunStatus.QUEUED)

    if reviewed:
        monkeypatch.setattr(plans, "prepare_reviewed_execution_target", prepared)
    else:
        monkeypatch.setattr(plans, "prepare_execution_target", prepared)
    monkeypatch.setattr(control, "start_prepared_plan", admitted)
    path = Path("never-read-while-mocked.yml")
    if reviewed:
        run = plans.run_reviewed(path, expected_execution_digest="digest", fleet_run_id="known")
    else:
        run = plans.run(path, fleet_run_id="known")
    assert run.fleet_run_id == "known"
    assert [item[0] for item in calls] == ["prepare", "admit"]
    assert calls[1] == ("admit", "known", FleetRunSource.MANUAL)


def test_original_nonproduction_service_keeps_explicit_nonproduction_ingress(monkeypatch):
    plans, control = _real_owner(provisioned_ids=None, authenticated=False)
    target = SimpleNamespace(source=PlanFileSource(sha256="a" * 64))
    monkeypatch.setattr(plans, "prepare_execution_target", lambda *a, **kw: target)
    monkeypatch.setattr(control, "start_prepared_plan", lambda *a, **kw: "admitted")
    assert plans.run(Path("missing.yml")) == "admitted"


def test_reenrollment_checked_by_original_owner_at_service_cut(monkeypatch):
    plans, control = _real_owner(provisioned_ids=("known",))
    checks = []
    original = control.require_plan_ingress_preparation

    def rechecking(run_id):
        checks.append(run_id)
        # Deliberate revocation in original enrollment between entrypoint
        # check and service resolution must block before source work.
        control._lifecycle._fleet_provisioner = None
        return original(run_id)

    monkeypatch.setattr(control, "require_plan_ingress_preparation", rechecking)
    with pytest.raises(TrustProtocolError):
        plans.run(Path("do-not-resolve.yml"), fleet_run_id="known")
    assert checks == ["known"]


def test_enrolled_service_transports_real_locked_resolver_target_to_original_fleet_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Real immutable Plan + goon.lock; Fleet effect owner is intentionally not run."""
    import hashlib

    from oh_goon.core.models.target import FleetPlanTarget
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
    from tests.factories.shipped_templates import GOON_TEMPLATES

    plans, control = _real_owner(provisioned_ids=("known",))
    plans._resolver = FleetPlanResolver(goon_templates=GOON_TEMPLATES)
    path = tmp_path / "plan.yml"
    path.write_text(
        "version: 1\ninputs:\n- release_channel\n- revision\ngoons:\n- python/release-metadata\n",
        encoding="utf-8",
    )
    inputs = {"release_channel": "beta", "revision": "abcdef1"}
    lock = plans.lock(path, inputs=inputs)
    observed = []

    def admission(target, **kwargs):
        assert isinstance(target, FleetPlanTarget)
        assert target.source.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
        assert target.identity.digest == lock.digest
        assert target.plan.plan_id
        observed.append((target, kwargs))
        return SimpleNamespace(
            fleet_run_id=kwargs["fleet_run_id"],
            target=target,
            status=FleetRunStatus.QUEUED,
        )

    monkeypatch.setattr(control, "start_prepared_plan", admission)
    queued = plans.run(path, inputs=inputs, fleet_run_id="known", repository_ids={"repo-1"})
    assert queued.fleet_run_id == "known"
    assert len(observed) == 1
    assert observed[0][1]["repository_ids"] == {"repo-1"}
    assert observed[0][1]["source"] is FleetRunSource.MANUAL
    assert (tmp_path / "goon.lock").exists()
    assert len(list(tmp_path.iterdir())) == 2  # no state, journal or runtime byproducts


def test_enrolled_reviewed_service_uses_original_digest_not_a_second_compiler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from oh_goon.core.models.target import FleetPlanTarget
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
    from tests.factories.shipped_templates import GOON_TEMPLATES

    plans, control = _real_owner(provisioned_ids=("known",))
    plans._resolver = FleetPlanResolver(goon_templates=GOON_TEMPLATES)
    path = tmp_path / "plan.yml"
    path.write_text(
        "version: 1\ninputs:\n- release_channel\n- revision\ngoons:\n- python/release-metadata\n",
        encoding="utf-8",
    )
    values = {"release_channel": "beta", "revision": "abcdef1"}
    canonical = plans.resolve(path, inputs=values)
    from oh_goon.core.execution_lock_identity import build_execution_lock

    reviewed_digest = build_execution_lock(canonical).digest
    seen = []

    def admission(target, **kwargs):
        assert isinstance(target, FleetPlanTarget)
        assert target.identity.digest == reviewed_digest
        seen.append(target)
        return SimpleNamespace(fleet_run_id="known", status=FleetRunStatus.QUEUED)

    monkeypatch.setattr(control, "start_prepared_plan", admission)
    accepted = plans.run_reviewed(
        path,
        expected_execution_digest=reviewed_digest,
        inputs=values,
        fleet_run_id="known",
        repository_ids={"repo-1"},
    )
    assert accepted.fleet_run_id == "known"
    assert len(seen) == 1
    assert not (tmp_path / "goon.lock").exists()  # reviewed path is read-only


def test_cli_to_real_plan_service_to_one_fleet_admission_returns_original_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
):
    """CLI parser + real resolver + original lifecycle; admission is effect-stubbed."""
    import json

    from oh_goon.cli_parser import build_parser
    from oh_goon.cli_runtime_plans import run_plan_command
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
    from tests.factories.shipped_templates import GOON_TEMPLATES

    plans, control = _real_owner(provisioned_ids=("known",))
    plans._resolver = FleetPlanResolver(goon_templates=GOON_TEMPLATES)
    path = tmp_path / "plan.yml"
    path.write_text(
        "version: 1\ninputs:\n- release_channel\n- revision\ngoons:\n- python/release-metadata\n",
        encoding="utf-8",
    )
    values = {"release_channel": "beta", "revision": "abcdef1"}
    lock = plans.lock(path, inputs=values)
    preflights, admitted = [], []
    original = control.require_plan_ingress_preparation

    def check(run_id):
        preflights.append(run_id)
        return original(run_id)

    monkeypatch.setattr(control, "require_plan_ingress_preparation", check)

    def admit(target, **kwargs):
        assert target.identity.digest == lock.digest
        admitted.append(target)
        return SimpleNamespace(
            fleet_run_id="known",
            target=target,
            status=FleetRunStatus.QUEUED,
        )

    monkeypatch.setattr(control, "start_prepared_plan", admit)
    args = build_parser().parse_args(
        [
            "dev",
            "_run-plan",
            str(path),
            "--repository-id",
            "repo-1",
            "--fleet-run-id",
            "known",
            "--lock-file",
            str(tmp_path / "goon.lock"),
            "--json",
            "--no-wait",
        ]
    )
    args.command, args.plan_command = "plan", "run"
    args._selected_inputs = values
    assert run_plan_command(SimpleNamespace(plans=plans), args) == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema": "oh-goon.plan-run-result/v1",
        "fleet_run_id": "known",
        "plan_id": admitted[0].plan.plan_id,
        "status": "QUEUED",
        "outcome": None,
    }
    assert len(admitted) == 1
    # Entry preflight protects caller inputs; service preflight protects callers
    # bypassing the CLI and catches a revoked identity before Plan resolution.
    assert preflights == ["known", "known"]


def test_cli_enrollment_revoked_after_preflight_refuses_before_plan_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
):
    import json

    from oh_goon.cli_parser import build_parser
    from oh_goon.cli_runtime_plans import run_plan_command

    plans, control = _real_owner(provisioned_ids=("known",))
    preflights = []
    original = control.require_plan_ingress_preparation

    def first_then_revoke(run_id):
        preflights.append(run_id)
        result = original(run_id)
        if len(preflights) == 1:
            control._lifecycle._fleet_provisioner = None
        return result

    monkeypatch.setattr(control, "require_plan_ingress_preparation", first_then_revoke)
    monkeypatch.setattr(
        control, "start_prepared_plan", lambda *a, **kw: pytest.fail("no admission")
    )
    plan = tmp_path / "not-readable-after-revoke.yml"
    plan.write_text("INVALID: [", encoding="utf-8")
    args = build_parser().parse_args(
        [
            "dev",
            "_run-plan",
            str(plan),
            "--repository-id",
            "repo-1",
            "--fleet-run-id",
            "known",
            "--json",
            "--lock-file",
            str(tmp_path / "goon.lock"),
        ]
    )
    args.command, args.plan_command = "plan", "run"
    assert run_plan_command(SimpleNamespace(plans=plans), args) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"
    assert preflights == ["known", "known"]
