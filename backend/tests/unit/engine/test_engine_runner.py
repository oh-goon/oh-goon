from pathlib import Path

import pytest

from oh_goon.core.errors import CapabilityUnavailable
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.models.target import UvRecipeTarget
from oh_goon.core.vocabulary import OperationAction, UvRecipeId
from oh_goon.engine.git import GitService
from oh_goon.engine.runner import EngineRunner
from tests.factories.capabilities import capability_request, execute_capability_runner
from tests.factories.operation_reports import make_operation_report


def _execution(context) -> OperationExecution:
    attempt = context.attempt
    report = make_operation_report(
        attempt.repository,
        attempt.action,
        target=attempt.target,
        fleet_run_id=attempt.fleet_run_id,
        operation_id=attempt.operation_id,
    )
    return OperationExecution(stdout="ok", stderr="", report=report)


def test_engine_runner_dispatches_native_operation_with_canonical_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    observed = {}

    def fake_run(context):
        observed["context"] = context
        return _execution(context)

    monkeypatch.setattr("oh_goon.engine.runner.init.run", fake_run)
    request = capability_request(
        repository, OperationAction.INIT, fleet_run_id="fleet-test", operation_id="op-engine"
    )
    execution = execute_capability_runner(EngineRunner(GitService(), timeout_seconds=5), request)

    context = observed["context"]
    assert context.attempt.repository.path == repository.resolve()
    assert context.attempt.fleet_run_id == "fleet-test"
    assert execution.report.outcome.reason_code is ReasonCode.READY
    assert execution.report.provenance.fleet_run_id == "fleet-test"


def test_engine_runner_rejects_non_engine_capability(tmp_path: Path) -> None:
    with pytest.raises(CapabilityUnavailable):
        target = UvRecipeTarget(kind="recipe", recipe=UvRecipeId.LOCK_CHECK)
        request = capability_request(tmp_path, OperationAction.RECIPE, target)
        execute_capability_runner(EngineRunner(GitService()), request)
