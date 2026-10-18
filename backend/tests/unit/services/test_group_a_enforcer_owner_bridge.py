"""M149 owner convergence and authenticated-contract identity bridge; no physical work."""

from dataclasses import replace

import pytest

from oh_goon.core.enforcer_lace_trust import AdmittedRunContract, TrustProtocolError
from oh_goon.core.execution_environment import ResolvedExecutionEnvironment
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.vocabulary import CommandSafety
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from tests.unit.core.test_enforcer_lace_trust import authority


def _environment(**overrides) -> ResolvedExecutionEnvironment:
    fields = dict(
        backend="bubblewrap", executor_id="executor-a", executor_generation=9,
        backend_configuration_identity="backend-config-a",
        capability_statement={"backend_id": "bubblewrap", "private_fs": True},
        deployment_authority={"scope": "isolated", "limits": [1, 2]},
    )
    fields.update(overrides)
    return ResolvedExecutionEnvironment.from_authorities(**fields)


def _contract(environment: ResolvedExecutionEnvironment) -> AdmittedRunContract:
    fields = authority()
    fields.update(
        execution_environment_identity=environment.execution_environment_identity,
        backend_capability_manifest_identity=environment.capability_manifest_identity,
        executor_generation=environment.executor_generation,
    )
    return AdmittedRunContract.freeze(fields)


def test_real_environment_identities_are_accepted_by_frozen_contract() -> None:
    env = _environment()
    assert env.execution_environment_identity.startswith("sha256:")
    assert env.capability_manifest_identity.startswith("sha256:")
    FleetAdmissionPlanner.require_exact_contract_environment(
        _contract(env), configured=env, current=_environment()
    )


@pytest.mark.parametrize("change", [
    {"executor_generation": 10},
    {"backend": "opensandbox", "capability_statement": {"backend_id": "opensandbox"}},
    {"capability_statement": {"backend_id": "bubblewrap", "private_fs": False}},
    {"deployment_authority": {"scope": "different"}},
    {"backend_configuration_identity": "new-config"},
])
def test_environment_drift_rejects_before_publication(change) -> None:
    env = _environment()
    with pytest.raises(FleetPlanInvalid, match="fresh admission"):
        FleetAdmissionPlanner.require_exact_contract_environment(
            _contract(env), configured=env, current=_environment(**change)
        )


@pytest.mark.parametrize("field,value", [
    ("execution_environment_identity", "sha256:" + "0" * 64),
    ("backend_capability_manifest_identity", "sha256:" + "1" * 64),
    ("executor_generation", 10),
])
def test_contract_with_different_backend_capability_or_generation_rejected(field, value) -> None:
    env = _environment()
    fields = _contract(env).fields
    fields[field] = value
    with pytest.raises(TrustProtocolError, match="exact configured"):
        FleetAdmissionPlanner.require_exact_contract_environment(
            AdmittedRunContract.freeze(fields), configured=env, current=_environment()
        )


def test_forged_candidate_fails_before_publication() -> None:
    env = _environment()
    with pytest.raises(TrustProtocolError):
        FleetAdmissionPlanner.require_exact_contract_environment(
            replace(_contract(env), identity="sha256:" + "f" * 64),
            configured=env, current=env,
        )


def test_common_enforcer_cut_orders_safety_before_quarantine_once() -> None:
    calls = []
    cut = object()

    class Safety:
        def admit_effect_cut(self):
            calls.append("admit")
            return cut
        def require_repository_effects_allowed(self, admitted, *, safety, repository_ids):
            assert admitted is cut
            assert safety is CommandSafety.WORKTREE_MUTATION
            assert repository_ids == ("repo-one",)
            calls.append("quarantine")

    assert FleetAdmissionPlanner.admit_repository_effect_cut(
        Safety(), safety=CommandSafety.WORKTREE_MUTATION, repository_ids=("repo-one",)
    ) is cut
    assert calls == ["admit", "quarantine"]


def test_common_enforcer_cut_rejection_prevents_second_authority() -> None:
    calls = []
    class Safety:
        def admit_effect_cut(self):
            calls.append("admit")
            raise ValueError("fail before work")
        def require_repository_effects_allowed(self, *args, **kwargs):
            calls.append("quarantine")
    with pytest.raises(ValueError, match="fail before work"):
        FleetAdmissionPlanner.admit_repository_effect_cut(
            Safety(), safety=CommandSafety.WORKTREE_MUTATION, repository_ids=("repo-one",)
        )
    assert calls == ["admit"]
