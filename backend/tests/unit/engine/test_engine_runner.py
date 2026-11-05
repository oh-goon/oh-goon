from pathlib import Path

import pytest

from oh_goon.core.errors import CapabilityUnavailable, StateConflict
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.operation import OperationExecution, RepositoryValidationCompleted
from oh_goon.core.models.target import UvRecipeTarget
from oh_goon.core.vocabulary import (
    AuditState,
    CheckState,
    OperationAction,
    UvRecipeId,
    ValidationState,
)
from oh_goon.engine.command import CommandResult
from oh_goon.engine.git import GitService
from oh_goon.engine.runner import EngineRunner
from tests.factories.capabilities import capability_request, execute_capability_runner
from tests.factories.operation_reports import make_operation_report
from tests.factories.policy import make_policy_snapshot


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


@pytest.mark.parametrize(
    ("action", "unavailable_at"),
    [
        (OperationAction.UPDATE, None),
        (OperationAction.UPDATE, 1),
        (OperationAction.UPDATE, 2),
        (OperationAction.UPDATE, 3),
        (OperationAction.PREPARE_REVIEW, None),
        (OperationAction.PREPARE_REVIEW, 1),
        (OperationAction.PREPARE_REVIEW, 2),
    ],
)
def test_mutating_operations_require_available_git_before_effects_and_afterward(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: OperationAction,
    unavailable_at: int | None,
) -> None:
    observations = 0
    effects: list[tuple[str, ...]] = []

    def native_git(_executable: str, *argv: str, **_kwargs: object) -> CommandResult:
        nonlocal observations
        if argv[0] == "status":
            observations += 1
            if observations == unavailable_at:
                return CommandResult(128, "", "Git status failed")
            changed = (
                "? uv.lock\0"
                if action is OperationAction.PREPARE_REVIEW and observations == 1
                else ""
            )
            return CommandResult(0, "# branch.oid (initial)\0# branch.head main\0" + changed, "")
        effects.append(argv)
        return CommandResult(0, "", "")

    def native_update(_repository: Path, *argv: str, **_kwargs: object) -> CommandResult:
        effects.append(argv)
        return CommandResult(0, "", "")

    monkeypatch.setattr(
        "oh_goon.engine.operations.update.policy_snapshot", lambda _path: make_policy_snapshot()
    )
    monkeypatch.setattr(
        "oh_goon.engine.operations.review.policy_snapshot", lambda _path: make_policy_snapshot()
    )
    monkeypatch.setattr("oh_goon.engine.operations.update.run_operation_command", native_update)
    monkeypatch.setattr(
        "oh_goon.engine.operations.update.validate_repository",
        lambda *_args: (
            RepositoryValidationCompleted(
                state="repository_completed",
                environment=ValidationState.VALID,
                security_audit=AuditState.PASSED,
                tests=CheckState.PASSED,
            ),
            "",
            "",
        ),
    )
    request = capability_request(tmp_path, action)
    runner = EngineRunner(GitService(command_runner=native_git))

    if unavailable_at is None:
        execution = execute_capability_runner(runner, request)
        assert execution.report.outcome.state == "successful"
        assert effects
    else:
        with pytest.raises(StateConflict, match="Git worktree observation is unavailable"):
            execute_capability_runner(runner, request)
        assert bool(effects) is (unavailable_at > 1)
