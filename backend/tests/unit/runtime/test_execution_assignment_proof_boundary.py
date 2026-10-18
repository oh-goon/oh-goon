from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from oh_goon.core.product_identity import stable_id
from oh_goon.core.models.execution_assignment import AttemptBudget, ExecutionAssignment


def payload(**overrides):
    data = dict(attempt_id="a", workspace_id="w", input_checkpoint_id="c", filesystem_authority="candidate_mutation", process_authority="isolated", network_authority="none", secret_grants=[], git_authority="none", publish_authority=False, tool_ids=[], delegation_limit=0, isolation_requirements=["private_filesystem"], input_artifact_ids=[], attempt_budget={"wall_time_seconds": 10, "max_processes": None})
    data.update(overrides)
    return data


def make(**overrides):
    p = payload(**overrides)
    return ExecutionAssignment(assignment_id="x", assignment_fingerprint=stable_id("execution-assignment", p), created_at=datetime.now(UTC), **p)


def test_candidate_mutation_cannot_hold_publish_authority():
    with pytest.raises(ValidationError, match="cannot carry publish authority"):
        make(publish_authority=True, network_authority="declared", input_artifact_ids=["proof-1"])


def test_publish_assignment_is_read_only_declared_network_and_proof_bound():
    assignment = make(filesystem_authority="read_only", publish_authority=True, network_authority="declared", input_artifact_ids=["proof-1"])
    assert assignment.publish_authority is True
    with pytest.raises(ValidationError, match="exact proof artifact"):
        make(filesystem_authority="read_only", publish_authority=True, network_authority="declared")
