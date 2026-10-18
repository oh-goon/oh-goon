"""WF-02 is a review cut: it must not activate trusted-local execution."""
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
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
    isolation_requirements,
)


@pytest.mark.parametrize("policy", [
    CicdExecutionPolicy(trust_repository_code=True),
    CicdExecutionPolicy(trust_repository_code=False),
    InspectionExecutionPolicy(),
    MaintenanceExecutionPolicy(),
    MigrationExecutionPolicy(),
    VerificationExecutionPolicy(),
])
def test_trusted_local_requires_separate_human_approval_for_every_purpose(policy):
    projection = compile_lace_required_guarantees(policy)
    assert projection.trusted_local_execution == "not_approved"
    assert projection.native_kernel_effects == "require_independent_custody"
    with pytest.raises(ValidationError):
        LaceRequiredGuarantees(
            purpose=projection.purpose,
            threat_model=projection.threat_model,
            isolation=projection.isolation,
            profile_status=projection.profile_status,
            trusted_local_execution="approved",
        )


def test_trusted_repo_setting_never_means_bare_host_execution():
    required = compile_lace_required_guarantees(
        CicdExecutionPolicy(trust_repository_code=True)
    ).require_defined()
    bare_host = IsolationBackendCapabilities(
        backend_id="bare-host-local",
        private_filesystem=False,
        private_process_tree=False,
        enforced_network_policy=False,
        content_identified_runtime=False,
        exact_argv=True,
        disposable_attempt=False,
        immutable_authority_after_start=False,
        enforced_cpu_limit=False,
        enforced_memory_limit=False,
        enforced_process_limit=False,
        kernel_boundary=IsolationKernelBoundary.SHARED_HOST_KERNEL,
    )
    assert not bare_host.satisfies(required)


def test_untrusted_and_trusted_repo_cannot_be_conflated():
    trusted = isolation_requirements(True)
    untrusted = isolation_requirements(False)
    assert trusted.minimum_kernel_boundary == IsolationKernelBoundary.SHARED_HOST_KERNEL
    assert untrusted.minimum_kernel_boundary == IsolationKernelBoundary.APPLICATION_KERNEL
    assert untrusted != trusted


def test_non_cicd_purposes_do_not_inherit_trusted_execution():
    for policy in (MaintenanceExecutionPolicy(), MigrationExecutionPolicy(),
                   VerificationExecutionPolicy(), InspectionExecutionPolicy()):
        demand = compile_lace_required_guarantees(policy)
        assert demand.profile_status == "not_defined"
        with pytest.raises(ValueError, match="policy review"):
            demand.require_defined()
