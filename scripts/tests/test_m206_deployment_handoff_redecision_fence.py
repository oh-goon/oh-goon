"""M206: deny mutable process/CLI redecisions before deployment APP workload."""

from __future__ import annotations

from typing import Any

from pathlib import Path

import pytest

from oh_goon import cli
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.sqlite_journal_vocabulary import (
    SQLITE_JOURNAL_DELETE,
    SQLITE_JOURNAL_MODE_ENV,
)
from oh_goon.core.strict_executor_authority import EXECUTORS_FILE_ENV
from oh_goon.services import certification_frontdoor as frontdoor
from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner


PLAN = "templates/plans/oh-goon/app-certification/plan.yml"
ARGS = [
    "plan",
    "run",
    PLAN,
    "--repository-id",
    "repo-a",
    "--lock-file",
    "/state/locks/app-certification.goon.lock",
    "--wait",
    "--repair-handoff",
]


def prepared(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[FleetPreworkProvisioner, dict[str, str], list[str]]:
    owner = object.__new__(FleetPreworkProvisioner)
    object.__setattr__(
        owner, "executor_inventory_file", Path("/deployment/inventory.toml")
    )
    monkeypatch.setenv(EXECUTORS_FILE_ENV, str(owner.executor_inventory_file))
    monkeypatch.setenv(SQLITE_JOURNAL_MODE_ENV, SQLITE_JOURNAL_DELETE)
    monkeypatch.setenv("OH_GOON_DB", "/deployment/db.sqlite")
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", "/deployment/league.toml")
    observed: list[str] = []

    def inspect(_self: object, run_id: str) -> dict[str, str]:
        observed.append(run_id)
        return {"status": "OBSERVED_NOT_ADMITTED"}

    monkeypatch.setattr(
        FleetPreworkProvisioner, "inspect_deployment_readiness", inspect
    )
    env = {
        "OH_GOON_DB": "/deployment/db.sqlite",
        "OH_GOON_LEAGUE_FILE": "/deployment/league.toml",
        SQLITE_JOURNAL_MODE_ENV: SQLITE_JOURNAL_DELETE,
    }
    return owner, env, observed


def test_exact_canonical_cli_handoff_and_original_reobservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, env, observed = prepared(monkeypatch)
    invoked = []

    def record_main(args: Any, *, fleet_provisioner: Any) -> int:
        invoked.append((args, fleet_provisioner))
        return 0

    monkeypatch.setattr(
        cli,
        "main",
        record_main,
    )
    assert (
        frontdoor._deployment_context_mismatch(
            env, fleet_provisioner=owner, fleet_run_id="run-a"
        )
        is None
    )
    assert frontdoor._run_deployment_plan(
        ARGS, env, fleet_provisioner=owner, fleet_run_id="run-a"
    ) == (0, "")
    assert observed == ["run-a", "run-a"]
    assert invoked == [([*ARGS, "--fleet-run-id", "run-a"], owner)]


@pytest.mark.parametrize(
    "attack",
    [
        [*ARGS, "--fleet-run-id", "run-forged"],
        [*ARGS, "--input", "override=x"],
        [*ARGS[:-1], "--no-wait"],
        [*ARGS[:6], "relative/lock.goon.lock", *ARGS[7:]],
        [*ARGS[:6], "/elsewhere/not-app.goon.lock", *ARGS[7:]],
        [*ARGS[:4], "--other", *ARGS[5:]],
        ["plan", "run", "templates/plans/oh-goon/prepare-release/plan.yml", *ARGS[3:]],
    ],
)
def test_mutated_cli_grammar_never_reaches_app(
    monkeypatch: pytest.MonkeyPatch, attack: list[str]
) -> None:
    owner, env, observed = prepared(monkeypatch)
    monkeypatch.setattr(
        cli, "main", lambda *_a, **_kw: pytest.fail("wrong CLI invoked")
    )
    rc, why = frontdoor._run_deployment_plan(
        attack, env, fleet_provisioner=owner, fleet_run_id="run-a"
    )
    assert rc == 2 and "canonical" in why.lower()
    assert observed == []


def test_journal_mode_drift_rejected_before_cli_or_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, env, observed = prepared(monkeypatch)
    monkeypatch.setenv(SQLITE_JOURNAL_MODE_ENV, "WAL")
    mismatch = frontdoor._deployment_context_mismatch(
        env, fleet_provisioner=owner, fleet_run_id="run-a"
    )
    assert mismatch is not None and "journal" in mismatch.lower()


def test_signer_custody_revoked_after_bootstrap_guard_rejected_before_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, env, observed = prepared(monkeypatch)
    assert (
        frontdoor._deployment_context_mismatch(
            env, fleet_provisioner=owner, fleet_run_id="run-a"
        )
        is None
    )
    assert observed == ["run-a"]

    def revoked(_self: object, _run_id: str) -> None:
        raise TrustProtocolError("deployment signer custody revoked after bootstrap")

    monkeypatch.setattr(
        FleetPreworkProvisioner, "inspect_deployment_readiness", revoked
    )
    monkeypatch.setattr(
        cli, "main", lambda *_a, **_kw: pytest.fail("wrong CLI invoked")
    )
    with pytest.raises(TrustProtocolError, match="custody revoked"):
        frontdoor._run_deployment_plan(
            ARGS, env, fleet_provisioner=owner, fleet_run_id="run-a"
        )


def test_revocation_before_bootstrap_never_calls_backend_or_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard runs before workflow's League add-folder and lock commands."""
    from oh_goon import workflow

    owner, env, observed = prepared(monkeypatch)

    def revoked(_self: object, _run_id: str) -> None:
        raise TrustProtocolError("original native signer revoked")

    monkeypatch.setattr(
        FleetPreworkProvisioner, "inspect_deployment_readiness", revoked
    )
    monkeypatch.setattr(
        workflow, "_backend_cli", lambda *_a, **_kw: pytest.fail("bootstrap mutated")
    )
    with pytest.raises(TrustProtocolError, match="signer revoked"):
        workflow._run_plan(
            plan=workflow.APP_PLAN,
            repo_id="repo-a",
            env=env,
            lock_root=Path("/state/locks"),
            deployment_context_guard=lambda state: frontdoor._deployment_context_mismatch(
                state, fleet_provisioner=owner, fleet_run_id="run-a"
            ),
            deployment_plan_run=lambda *_a: pytest.fail("Plan invoked"),
        )
    assert observed == []


def test_inventory_change_at_prebootstrap_guard_prevents_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oh_goon import workflow

    owner, env, observed = prepared(monkeypatch)
    monkeypatch.setenv(EXECUTORS_FILE_ENV, "/attacker/inventory.toml")
    monkeypatch.setattr(
        workflow, "_backend_cli", lambda *_a, **_kw: pytest.fail("bootstrap mutated")
    )
    rc, why = workflow._run_plan(
        plan=workflow.APP_PLAN,
        repo_id="repo-a",
        env=env,
        lock_root=Path("/state/locks"),
        deployment_context_guard=lambda state: frontdoor._deployment_context_mismatch(
            state, fleet_provisioner=owner, fleet_run_id="run-a"
        ),
        deployment_plan_run=lambda *_a: pytest.fail("Plan invoked"),
    )
    assert rc == 2 and "inventory" in why
    assert observed == []
