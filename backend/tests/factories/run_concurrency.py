"""Passive database-admission fixtures; these never establish native resource custody."""

import hashlib

from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.run_resources import (
    NativeRuntimeSliceEvidence,
    RunConcurrencyAdmission,
    RunConcurrencyPolicy,
)
from oh_goon.core.process_birth import observe_process_birth
from oh_goon.integrations.run_resource_envelope import (
    DeferredWorkerResourceScope,
    ResolvedNativeRuntimeBudget,
    ResolvedRunResourceScope,
)


def run_concurrency_admission(run_id: str) -> RunConcurrencyAdmission:
    return run_concurrency_scope(run_id).admission


def run_concurrency_scope(run_id: str) -> DeferredWorkerResourceScope:
    birth = observe_process_birth()
    assert birth is not None
    aggregate = CicdResourcePolicy(cpu_millis=2500, memory_mib=1024, max_processes=128)
    policy = RunConcurrencyPolicy(
        aggregate_slice="oh-goon-admission-fixture.slice",
        aggregate_resources=aggregate,
        worker_resources=CicdResourcePolicy(cpu_millis=1000, memory_mib=384, max_processes=48),
        controller_reserve=CicdResourcePolicy(cpu_millis=500, memory_mib=256, max_processes=32),
    )
    aggregate_evidence = NativeRuntimeSliceEvidence(
        unit_name=policy.aggregate_slice,
        invocation_id="a" * 32,
        controller_birth=birth,
        controller_cgroup_device=1,
        controller_cgroup_inode=2,
        cgroup_device=1,
        cgroup_inode=3,
        resources=aggregate,
        cpu_max="250000 100000",
        memory_max_bytes=1024 * 1024 * 1024,
        memory_swap_max_bytes=0,
        pids_max=128,
    )
    unit = "oh-goon-run-" + hashlib.sha256(run_id.encode()).hexdigest()[:32] + ".scope"
    scope = ResolvedRunResourceScope(
        run_id,
        policy.worker_resources,
        unit,
        "/fixture/systemd-run",
        "b" * 64,
        "/fixture/systemctl",
        "c" * 64,
        (),
        (
            "/fixture/systemd-run",
            f"--slice={policy.aggregate_slice}",
            f"--unit={unit}",
            "--expand-environment=no",
            "--",
        ),
        policy.aggregate_slice,
    )
    budget = ResolvedNativeRuntimeBudget(
        policy,
        aggregate_evidence,
        scope.systemd_run,
        scope.systemd_run_sha256,
        scope.systemctl,
        scope.systemctl_sha256,
        scope.manager_environment,
        "/fixture/journalctl",
        "d" * 64,
    )
    return DeferredWorkerResourceScope.create(scope, budget)
