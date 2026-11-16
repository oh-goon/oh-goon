"""Contention retains the execution owner's safe code across CLI presentation."""

from __future__ import annotations

import json

import pytest

from oh_goon.cli_plan_diagnostics import print_safe_plan_diagnostic
from oh_goon.core.errors import DurableStateBusy, ExecutionBusy
from oh_goon.core.plan_validation_diagnostics import safe_plan_diagnostic


@pytest.mark.parametrize(
    "rejection,code,stage",
    (
        (ExecutionBusy, "EXECUTION_BUSY", "execution_admission"),
        (DurableStateBusy, "PLAN_RESOLUTION_REJECTED", "plan_resolution"),
    ),
)
def test_execution_contention_keeps_owner_code_without_private_details(
    rejection: type[ExecutionBusy] | type[DurableStateBusy], code: str, stage: str
) -> None:
    diagnostic = safe_plan_diagnostic(
        rejection("PRIVATE_RUN_TOKEN /private/state/db.sqlite already active")
    )
    assert diagnostic["code"] == code
    assert diagnostic["stage"] == stage
    assert diagnostic["inputs"] == []
    assert diagnostic["execution"] == "not_started_by_diagnostic"
    assert "Wait for active execution to settle" in diagnostic["safe_next_action"]
    encoded = json.dumps(diagnostic)
    assert "PRIVATE_RUN_TOKEN" not in encoded and "/private/state" not in encoded


def test_text_execution_contention_does_not_describe_an_invalid_plan(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_safe_plan_diagnostic(ExecutionBusy("PRIVATE_RUN_TOKEN /private/state"), as_json=False)
    text = capsys.readouterr().out
    assert text.startswith("Execution busy: EXECUTION_BUSY\n")
    assert "Wait for active execution to settle" in text
    assert "Plan rejected" not in text and "PRIVATE_RUN_TOKEN" not in text
