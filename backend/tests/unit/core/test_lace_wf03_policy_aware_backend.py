"""WF-03: policy eligibility can never become a silent strict downgrade.

These are compiler/owner tests only.  No host-execution qualification is implied.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
)
from oh_goon.core.models.lace_trusted_local import TrustedLocalRunConsent
from oh_goon.integrations.fleet_plan_lace_policy import require_policy_aware_lace_backend

RUN = "run-1"
DIGEST = "a" * 64


def _backend(**changes):
    data = dict(
        backend_id="trusted-local",
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
        remote_execution=False,
    )
    data.update(changes)
    return IsolationBackendCapabilities(**data)


def _consent(**changes):
    data = dict(
        fleet_run_id=RUN,
        execution_digest=DIGEST,
        backend_id="trusted-local",
        backend_configuration_id="config-1",
        executor_id="local-1",
        executor_generation=2,
        operator_interactive=True,
        explicitly_acknowledged_ambient_host_access=True,
    )
    data.update(changes)
    return TrustedLocalRunConsent(**data)


def _authorize(*, execution=None, backend=None, consent=None, **changes):
    facts = {
        "source": "manual",
        "all_repositories": False,
        "repository_count": 1,
        "unattended": False,
    }
    facts.update(changes)
    return require_policy_aware_lace_backend(
        execution=execution or CicdExecutionPolicy(),
        backend=backend or _backend(),
        consent=consent,
        fleet_run_id=RUN,
        execution_digest=DIGEST,
        backend_configuration_id="config-1",
        executor_id="local-1",
        executor_generation=2,
        **facts,
    )


def test_default_bare_host_cannot_satisfy_strict_requirements():
    with pytest.raises(FleetPlanInvalid, match="strict LACE guarantees"):
        _authorize()


def test_explicit_tl1_eligibility_does_not_claim_sandbox_requirements():
    assert _authorize(consent=_consent()) is None
    consent = _consent()
    assert consent.filesystem_access == "operator_host_permissions"
    assert consent.network_access == "operator_host_permissions"
    assert consent.process_access == "operator_host_permissions"


@pytest.mark.parametrize(
    "field,changed",
    [
        ("fleet_run_id", "other"),
        ("execution_digest", "b" * 64),
        ("backend_id", "other"),
        ("backend_configuration_id", "old"),
        ("executor_id", "old"),
        ("executor_generation", 3),
    ],
)
def test_stale_consent_refused(field, changed):
    with pytest.raises(FleetPlanInvalid, match="stale"):
        _authorize(consent=_consent(**{field: changed}))


@pytest.mark.parametrize(
    "execution",
    [
        CicdExecutionPolicy(trust_repository_code=False),
        CicdExecutionPolicy(resources=CicdResourcePolicy(cpu_millis=500)),
        CicdExecutionPolicy(resources=CicdResourcePolicy(memory_mib=128)),
        CicdExecutionPolicy(resources=CicdResourcePolicy(max_processes=32)),
    ],
)
def test_untrusted_or_kernel_resource_capped_never_eligible(execution):
    with pytest.raises(FleetPlanInvalid, match="trusted serial CI/CD"):
        _authorize(consent=_consent(), execution=execution)


@pytest.mark.parametrize(
    "change",
    [
        {"unattended": True},
        {"source": "scheduled"},
        {"source": "agent"},
        {"all_repositories": True},
        {"repository_count": 2},
        {"repository_count": 0},
    ],
)
def test_unattended_or_unscoped_never_eligible(change):
    with pytest.raises(FleetPlanInvalid, match="interactive operator-selected"):
        _authorize(consent=_consent(), **change)


@pytest.mark.parametrize(
    "backend",
    [
        _backend(backend_id="bubblewrap"),
        _backend(backend_id="opensandbox"),
        _backend(remote_execution=True),
        _backend(enforced_network_policy=True),
        _backend(private_process_tree=True),
        _backend(exact_argv=False),
    ],
)
def test_backend_cannot_misrepresent_its_physical_guarantees(backend):
    consent = (
        _consent(backend_id=backend.backend_id)
        if backend.backend_id not in {"bubblewrap", "opensandbox"}
        else _consent()
    )
    with pytest.raises((FleetPlanInvalid, ValidationError)):
        _authorize(consent=consent, backend=backend)


def test_consent_cannot_claim_strict_backend_or_noninteractive_authority():
    for change in (
        {"backend_id": "bubblewrap"},
        {"backend_id": "opensandbox"},
        {"operator_interactive": False},
        {"explicitly_acknowledged_ambient_host_access": False},
    ):
        with pytest.raises(ValidationError):
            _consent(**change)


def test_consent_is_not_reusable_or_mutable():
    consent = _consent()
    with pytest.raises(ValidationError):
        consent.fleet_run_id = "other"
    with pytest.raises(ValidationError):
        TrustedLocalRunConsent.model_validate({**consent.model_dump(), "policy": "any"})


def test_no_authority_facts_refuse_before_any_backend_selection():
    with pytest.raises(FleetPlanInvalid, match="exact original Run"):
        require_policy_aware_lace_backend(
            execution=CicdExecutionPolicy(), backend=_backend(), consent=_consent()
        )


def test_strict_capability_still_enforces_original_boolean_demands():
    backend = _backend(
        backend_id="bubblewrap",
        private_filesystem=True,
        private_process_tree=True,
        enforced_network_policy=True,
        content_identified_runtime=True,
        disposable_attempt=True,
        immutable_authority_after_start=True,
        enforced_cpu_limit=True,
        enforced_memory_limit=True,
        enforced_process_limit=True,
    )
    strict = _authorize(backend=backend)
    assert strict is not None
    assert strict.private_filesystem and strict.enforced_network_policy
