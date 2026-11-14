from __future__ import annotations

from dataclasses import replace

import pytest

from oh_goon.repository_tooling import check_architecture as owner
from oh_goon.repository_tooling.architecture.common import Violation
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.pytest_resource_budget import (
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
        cgroup_usage_started=PytestCgroupUsageSnapshot(
            "native", True, None, None, None
        ),
    )
    monkeypatch.setattr(
        owner, "resolve_pytest_resource_budget", lambda *args, **kwargs: budget
    )
    return budget


def test_ordinary_architecture_keeps_original_transport_without_profile_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*args: object, **kwargs: object) -> PytestResourceBudget:
        pytest.fail("ordinary architecture requested profile admission")

    monkeypatch.setattr(owner, "resolve_pytest_resource_budget", unexpected)
    assert owner.captured_source_packets_enabled({}) is False


@pytest.mark.parametrize("available", [None, 0, 1024**3])
def test_one_use_handoff_does_not_require_extra_memory_or_node_cache(
    admitted_budget: PytestResourceBudget,
    monkeypatch: pytest.MonkeyPatch,
    available: int | None,
) -> None:
    budget = replace(
        admitted_budget,
        host_available_memory_bytes=available,
        available_memory_bytes=available,
    )
    monkeypatch.setattr(
        owner, "resolve_pytest_resource_budget", lambda *args, **kwargs: budget
    )
    assert (
        owner.captured_source_packets_enabled(
            {
                "OH_GOON_LOCAL_TEST_PROFILE": "ram",
                "OH_GOON_TEST_CPUS": "2",
                "NODE_DISABLE_COMPILE_CACHE": "1",
            }
        )
        is True
    )


@pytest.mark.parametrize(
    "fault",
    [
        "unresolved",
        "unknown-quota",
        "quota-too-high",
        "nonpositive-quota",
        "unbound",
        "unknown-pid",
    ],
)
def test_ram_profile_refuses_unverified_cpu_authority(
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
    elif fault == "nonpositive-quota":
        budget = replace(budget, cgroup_cpu_capacity=0)
    elif fault == "unbound":
        budget = replace(budget, cgroup_usage_started=replace(usage, pid_bound=False))
    else:
        budget = replace(budget, cgroup_usage_started=None)
    monkeypatch.setattr(
        owner, "resolve_pytest_resource_budget", lambda *args, **kwargs: budget
    )
    with pytest.raises(ValueError, match="positive native CPU quota"):
        owner.captured_source_packets_enabled(
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

    def check() -> list[Violation]:
        effects.append("started")
        return []

    monkeypatch.setattr(owner, "CHECKS", (check,))
    assert owner.main() == 3 and effects == []


def test_native_handoff_does_not_hide_architecture_failure(
    admitted_budget: PytestResourceBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OH_GOON_LOCAL_TEST_PROFILE", "ram")
    effects: list[str] = []

    def check() -> list[Violation]:
        effects.append("started")
        return [Violation(ROOT / "backend/pyproject.toml", "observed violation")]

    monkeypatch.setattr(owner, "CHECKS", (check,))
    assert owner.main() == 1 and effects == ["started"]


@pytest.mark.parametrize("profile", ["RAM", "unknown", " "])
def test_receiving_architecture_owner_refuses_an_unknown_profile(profile: str) -> None:
    with pytest.raises(ValueError, match="Unknown local test profile"):
        owner.captured_source_packets_enabled({"OH_GOON_LOCAL_TEST_PROFILE": profile})
