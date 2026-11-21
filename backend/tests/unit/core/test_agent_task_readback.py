"""Captured goal context stays bounded, redacted and separate from authority."""

import hashlib

import pytest
from pydantic import ValidationError

from oh_goon.core.agent_harness_contract import captured_task_readback
from oh_goon.core.models.fleet_plan import ResolvedAgentStep


def test_original_task_identity_survives_redaction_and_explicit_omission() -> None:
    task = "Review token=PRIVATE_VALUE\n" + "current goal " * 300
    context = captured_task_readback(task)
    assert context["task_sha256"] == hashlib.sha256(task.encode()).hexdigest()
    assert context["task_summary"].startswith("Review token=[REDACTED]\n")
    assert "PRIVATE_VALUE" not in context["task_summary"]
    assert len(context["task_summary"]) == 2048
    assert context["task_redacted"] and context["task_omitted_characters"] > 0


@pytest.mark.parametrize("field", ["task_summary", "task_sha256"])
def test_task_context_cannot_be_admitted_without_its_paired_identity(field: str) -> None:
    values = {
        "step_id": "search",
        "timeout_seconds": 10,
        "workload": {"harness": "local-search"},
        "delivery_sha256": "a" * 64,
        "handoff_identity": "captured-handoff",
        "source_step_id": "search",
        **captured_task_readback("search: bounded goal"),
    }
    values[field] = None
    with pytest.raises(ValidationError, match="summary and identity"):
        ResolvedAgentStep.model_validate(values)
