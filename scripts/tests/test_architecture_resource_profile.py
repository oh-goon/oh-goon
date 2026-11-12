from __future__ import annotations

from dataclasses import replace

import pytest

from oh_goon.repository_tooling import check_architecture as owner
from oh_goon.repository_tooling.pytest_resource_budget import (
    PytestCgroupObservation,
    PytestCgroupUsageSnapshot,
    PytestResourceBudget,
    resolve_pytest_resource_budget,
)


@pytest.fixture
def admitted_budget(monkeypatch: pytest.MonkeyPatch) -> PytestResourceBudget:
    native = resolve_pytest_resource_budget(1, environ={"OH_GOON_TEST_CPUS": "2"})
    budget = replace(
        native,
        cgroup_cpu_capacity=2,
        host_available_memory_bytes=32 * 1024**3,
        available_memory_bytes=32 * 1024**3,
        cgroup_usage_started=PytestCgroupUsageSnapshot(
            "native", True, None, None, None
        ),
        cgroups=(
            PytestCgroupObservation(
                "native", "200000 100000", 2, None, 0, True, None, True
            ),
        ),
    )
    monkeypatch.setattr(
        owner, "resolve_pytest_resource_budget", lambda *args, **kwargs: budget
    )
    return budget


def test_ordinary_architecture_does_not_admit_retention_or_probe_profile_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*args: object, **kwargs: object) -> PytestResourceBudget:
        pytest.fail("ordinary architecture requested profile resource admission")

    monkeypatch.setattr(owner, "resolve_pytest_resource_budget", unexpected)
    assert owner.source_packet_cache_budget({}) == 0


def test_admitted_ram_profile_uses_bounded_retention_independently_of_node_settings(
    admitted_budget: PytestResourceBudget,
) -> None:
    assert (
        owner.source_packet_cache_budget(
            {
                "OH_GOON_LOCAL_TEST_PROFILE": "ram",
                "OH_GOON_TEST_CPUS": "2",
                "NODE_DISABLE_COMPILE_CACHE": "1",
            }
        )
        == owner.SOURCE_PACKET_CACHE_BYTES
    )


@pytest.mark.parametrize(
    "fault",
    [
        "host",
        "effective",
        "host-unknown",
        "effective-unknown",
        "limits",
        "high",
        "current",
        "high-headroom",
        "no-groups",
    ],
)
def test_ram_retention_declines_unknown_or_insufficient_memory(
    admitted_budget: PytestResourceBudget, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    budget = admitted_budget
    row = budget.cgroups[0]
    if fault == "host":
        budget = replace(
            budget,
            host_available_memory_bytes=owner.SOURCE_PACKET_MINIMUM_AVAILABLE_BYTES - 1,
        )
    elif fault == "effective":
        budget = replace(
            budget,
            available_memory_bytes=owner.SOURCE_PACKET_MINIMUM_AVAILABLE_BYTES - 1,
        )
    elif fault == "host-unknown":
        budget = replace(budget, host_available_memory_bytes=None)
    elif fault == "effective-unknown":
        budget = replace(budget, available_memory_bytes=None)
    elif fault == "limits":
        budget = replace(budget, cgroups=(replace(row, memory_limit_observed=False),))
    elif fault == "high":
        budget = replace(budget, cgroups=(replace(row, memory_high_observed=False),))
    elif fault == "current":
        budget = replace(
            budget,
            cgroups=(
                replace(row, memory_max_bytes=32 * 1024**3, memory_current_bytes=None),
            ),
        )
    elif fault == "high-headroom":
        budget = replace(
            budget,
            cgroups=(
                replace(
                    row,
                    memory_high_bytes=32 * 1024**3,
                    memory_current_bytes=17 * 1024**3,
                ),
            ),
        )
    else:
        budget = replace(budget, cgroups=())
    monkeypatch.setattr(
        owner, "resolve_pytest_resource_budget", lambda *args, **kwargs: budget
    )
    assert (
        owner.source_packet_cache_budget(
            {"OH_GOON_LOCAL_TEST_PROFILE": "ram", "OH_GOON_TEST_CPUS": "2"}
        )
        == 0
    )


@pytest.mark.parametrize(
    "fault", ["unresolved", "unknown-quota", "quota-too-high", "unbound", "unknown-pid"]
)
def test_ram_profile_refuses_unverified_cpu_authority_before_architecture_work(
    admitted_budget: PytestResourceBudget, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    budget = admitted_budget
    usage = budget.cgroup_usage_started
    assert usage is not None
    if fault == "unresolved":
        budget = replace(budget, configured_cpus=0)
    elif fault == "unknown-quota":
        budget = replace(budget, cgroup_cpu_capacity=None)
    elif fault == "quota-too-high":
        budget = replace(budget, cgroup_cpu_capacity=3)
    elif fault == "unbound":
        budget = replace(
            budget,
            cgroup_usage_started=replace(usage, pid_bound=False),
        )
    else:
        budget = replace(budget, cgroup_usage_started=None)
    monkeypatch.setattr(
        owner, "resolve_pytest_resource_budget", lambda *args, **kwargs: budget
    )
    with pytest.raises(ValueError, match="positive native CPU quota"):
        owner.source_packet_cache_budget(
            {"OH_GOON_LOCAL_TEST_PROFILE": "ram", "OH_GOON_TEST_CPUS": "2"}
        )


def test_unverified_ram_cpu_refuses_before_any_architecture_check(
    admitted_budget: PytestResourceBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OH_GOON_LOCAL_TEST_PROFILE", "ram")
    monkeypatch.setattr(
        owner,
        "resolve_pytest_resource_budget",
        lambda *args, **kwargs: replace(admitted_budget, cgroup_cpu_capacity=None),
    )
    effects: list[str] = []

    def check() -> list[owner.Violation]:
        effects.append("started")
        return []

    monkeypatch.setattr(owner, "CHECKS", (check,))
    assert owner.main() == 3
    assert effects == []


def test_low_memory_retention_decline_preserves_architecture_failure(
    admitted_budget: PytestResourceBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OH_GOON_LOCAL_TEST_PROFILE", "ram")
    monkeypatch.setattr(
        owner,
        "resolve_pytest_resource_budget",
        lambda *args, **kwargs: replace(admitted_budget, available_memory_bytes=1),
    )
    effects: list[str] = []

    def check() -> list[owner.Violation]:
        effects.append("started")
        return [
            owner.Violation(owner.ROOT / "backend/pyproject.toml", "observed violation")
        ]

    monkeypatch.setattr(owner, "CHECKS", (check,))
    assert owner.main() == 1
    assert effects == ["started"]
