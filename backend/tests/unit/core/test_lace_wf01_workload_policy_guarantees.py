"""WF-01: workload/policy-specific demands; no native capability is issued."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
    InspectionExecutionPolicy,
    MaintenanceExecutionPolicy,
    MigrationExecutionPolicy,
    VerificationExecutionPolicy,
    LaceRequiredGuarantees,
    compile_lace_required_guarantees,
)
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
    IsolationThreatModel,
    isolation_requirements,
)


@pytest.mark.parametrize(
    ("threat", "boundary"),
    [
        (IsolationThreatModel.TRUSTED_REPOSITORY, IsolationKernelBoundary.SHARED_HOST_KERNEL),
        (IsolationThreatModel.UNTRUSTED_REPOSITORY, IsolationKernelBoundary.APPLICATION_KERNEL),
        (IsolationThreatModel.HOSTILE_GENERATED_CODE, IsolationKernelBoundary.VIRTUAL_MACHINE),
    ],
)
def test_explicit_threat_model_never_downgrades_to_truthy_trusted(threat, boundary):
    assert isolation_requirements(threat).minimum_kernel_boundary is boundary


def test_original_boolean_trust_semantics_preserved():
    assert isolation_requirements(True) == isolation_requirements(IsolationThreatModel.TRUSTED_REPOSITORY)
    assert isolation_requirements(False) == isolation_requirements(IsolationThreatModel.UNTRUSTED_REPOSITORY)


@pytest.mark.parametrize("invalid", ["trusted_repository", "untrusted_repository", "hostile_generated_code", 1, 0, None, object()])
def test_unsupported_truthy_or_falsey_coercion_rejected_before_placement(invalid):
    with pytest.raises(ValueError, match="boolean trust policy or explicit threat model"):
        isolation_requirements(invalid)


def test_hostile_model_cannot_use_shared_host_or_application_kernel():
    requirements = isolation_requirements(IsolationThreatModel.HOSTILE_GENERATED_CODE)
    backend = IsolationBackendCapabilities(
        backend_id="test", private_filesystem=True, private_process_tree=True,
        enforced_network_policy=True, content_identified_runtime=True, exact_argv=True,
        disposable_attempt=True, immutable_authority_after_start=True,
        enforced_cpu_limit=True, enforced_memory_limit=True, enforced_process_limit=True,
        kernel_boundary=IsolationKernelBoundary.APPLICATION_KERNEL,
    )
    assert not backend.satisfies(requirements)
    assert backend.model_copy(update={"kernel_boundary": IsolationKernelBoundary.VIRTUAL_MACHINE}).satisfies(requirements)


@pytest.mark.parametrize("trusted, boundary", [(True, IsolationKernelBoundary.SHARED_HOST_KERNEL), (False, IsolationKernelBoundary.APPLICATION_KERNEL)])
def test_cicd_guarantees_are_derived_from_canonical_trust_and_resources(trusted, boundary):
    caps = CicdResourcePolicy(cpu_millis=3000, max_processes=64)
    policy = CicdExecutionPolicy(trust_repository_code=trusted, resources=caps)
    demand = compile_lace_required_guarantees(policy)
    assert demand.profile_status == "defined"
    assert demand.threat_model is (IsolationThreatModel.TRUSTED_REPOSITORY if trusted else IsolationThreatModel.UNTRUSTED_REPOSITORY)
    assert demand.require_defined() == isolation_requirements(trusted, caps)
    assert demand.isolation.minimum_kernel_boundary is boundary
    assert demand.isolation.enforced_cpu_limit
    assert not demand.isolation.enforced_memory_limit
    assert demand.isolation.enforced_process_limit
    assert demand.native_kernel_effects == "require_independent_custody"
    assert demand.trusted_local_execution == "not_approved"


@pytest.mark.parametrize("policy", [
    MaintenanceExecutionPolicy(), MigrationExecutionPolicy(),
    VerificationExecutionPolicy(), InspectionExecutionPolicy(),
])
def test_other_purposes_have_explicit_unreviewed_profiles_not_no_guarantees(policy):
    demand = compile_lace_required_guarantees(policy)
    assert demand.purpose == policy.purpose
    assert demand.threat_model is None
    assert demand.isolation is None
    assert demand.profile_status == "not_defined"
    assert demand.native_kernel_effects == "require_independent_custody"
    assert demand.trusted_local_execution == "not_approved"
    with pytest.raises(ValueError, match="policy review is required"):
        demand.require_defined()


def test_demand_is_immutable_and_cannot_self_approve_native_or_trusted_local():
    demand = compile_lace_required_guarantees(CicdExecutionPolicy())
    with pytest.raises(ValidationError):
        demand.trusted_local_execution = "approved"
    with pytest.raises(ValidationError):
        LaceRequiredGuarantees(
            purpose=demand.purpose, threat_model=demand.threat_model,
            isolation=demand.isolation, profile_status="defined",
            trusted_local_execution="approved",
        )


def test_unrecognized_execution_policy_rejected():
    with pytest.raises(ValueError, match="Unsupported canonical Plan"):
        compile_lace_required_guarantees(object())
