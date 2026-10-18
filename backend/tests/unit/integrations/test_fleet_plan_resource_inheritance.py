from __future__ import annotations

import pytest
from pydantic import ValidationError

from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy, isolation_requirements


def test_cicd_resource_envelope_inherits_executor_capacity_until_explicitly_capped() -> None:
    policy = CicdExecutionPolicy.model_validate({"purpose": "cicd"})
    assert policy.resources == CicdResourcePolicy()
    assert policy.resources.cpu_millis is None
    assert policy.resources.memory_mib is None
    assert policy.resources.max_processes is None

    partial = CicdExecutionPolicy.model_validate(
        {"purpose": "cicd", "resources": {"memory_mib": 4096}}
    )
    assert partial.resources.cpu_millis is None
    assert partial.resources.memory_mib == 4096
    assert partial.resources.max_processes is None

    with pytest.raises(ValidationError):
        CicdResourcePolicy(cpu_millis=0, memory_mib=4096, max_processes=256)
    with pytest.raises(ValidationError):
        CicdResourcePolicy(cpu_millis=2000, memory_mib=32, max_processes=256)


def test_process_limit_is_inherited_until_explicitly_overridden() -> None:
    inherited = CicdResourcePolicy()
    with_pids = inherited.model_copy(update={"max_processes": 128})

    assert inherited.max_processes is None
    assert isolation_requirements(True, inherited).enforced_process_limit is False
    assert isolation_requirements(True, with_pids).enforced_process_limit is True
