"""Recovery declarations convey verifier semantics without changing native success."""

from typing import Any

import pytest
from pydantic import ValidationError

from oh_goon.core.models.fleet_plan import ResolvedCommandStep


def _declaration() -> dict[str, Any]:
    return {
        "step_id": "check",
        "argv": ["./check"],
        "outputs": [{"name": "diagnostic", "stream": "stdout", "max_bytes": 2048, "retain": True}],
        "regression_failure": {
            "exit_codes": [1],
            "feedback": "diagnostic",
            "reason": "Verifier exit1 reports failed code assertions",
        },
    }


def test_declared_failure_remains_native_failure_with_ordinary_stop() -> None:
    step = ResolvedCommandStep.model_validate(_declaration())
    assert step.success_codes == (0,)
    assert step.on_failure.value == "stop"
    assert step.regression_failure is not None and step.regression_failure.exit_codes == (1,)


@pytest.mark.parametrize("codes", [[0], [-1], [True], [124], [125], [126], [127], [256], [1, 1]])
def test_unknown_or_reserved_exits_are_not_a_verifier_contract(codes: list[object]) -> None:
    values = _declaration()
    values["regression_failure"]["exit_codes"] = codes
    with pytest.raises(ValidationError):
        ResolvedCommandStep.model_validate(values)


@pytest.mark.parametrize("attack", ["accepted", "missing", "file", "hash_only", "retry", "uv"])
def test_incomplete_recovery_declaration_is_refused_before_execution(attack: str) -> None:
    values = _declaration()
    if attack == "accepted":
        values["success_codes"] = [0, 1]
    elif attack == "missing":
        values["outputs"] = []
    elif attack == "file":
        values["outputs"].append({"name": "old_file", "path": "diagnostic.txt"})
    elif attack == "hash_only":
        values["outputs"][0]["retain"] = False
    elif attack == "retry":
        values["retry"] = {
            "max_attempts": 2,
            "known_transient_exit_codes": [1],
            "reason": "replay",
            "delays_seconds": [0],
        }
    else:
        values["uv"] = {"lock_mode": "frozen"}
    with pytest.raises(ValidationError):
        ResolvedCommandStep.model_validate(values)
