from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import InvalidRunTransition
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunFailure
from oh_goon.core.models.run_admission import fleet_repository_scope
from oh_goon.core.models.run_events import (
    FleetRunEvent,
    RunAdmittedEvent,
    RunFailedEvent,
    RunResourcesObservedEvent,
    RunStartedEvent,
)
from oh_goon.core.models.run_resources import RunResourceEnvelopeEvidence
from oh_goon.core.product_identity import repository_id
from oh_goon.core.run_projection import project_fleet_run_detail
from oh_goon.core.vocabulary import FleetRunEventType, FleetRunStatus
from tests.factories.run_resources import (
    run_resource_admission,
    run_resource_envelope,
    run_resource_observation,
)


def _history(tmp_path: Path, expected: RunResourceEnvelopeEvidence) -> list[FleetRunEvent]:
    path = tmp_path / "repo"
    repository = RepositoryIdentity(repository_id=repository_id(path), name="repo", path=path)
    intent = run_resource_admission(repository, expected)
    return [
        RunAdmittedEvent(
            event_type=FleetRunEventType.RUN_ADMITTED,
            sequence=1,
            fleet_run_id=expected.fleet_run_id,
            target=intent.target,
            capability=intent.capability,
            execution=intent.execution,
            repository_admissions=intent.repository_admissions,
            repository_scope=fleet_repository_scope({repository.repository_id}),
            targets=(repository,),
        ),
        RunStartedEvent(
            event_type=FleetRunEventType.RUN_STARTED,
            sequence=2,
            fleet_run_id=expected.fleet_run_id,
        ),
        RunFailedEvent(
            event_type=FleetRunEventType.RUN_FAILED,
            sequence=3,
            fleet_run_id=expected.fleet_run_id,
            failure=FleetRunFailure.recovery("Original workload failure"),
        ),
    ]


def _observed(expected: RunResourceEnvelopeEvidence, sequence: int) -> RunResourcesObservedEvent:
    return RunResourcesObservedEvent(
        event_type=FleetRunEventType.RUN_RESOURCES_OBSERVED,
        sequence=sequence,
        fleet_run_id=expected.fleet_run_id,
        observation=run_resource_observation(expected),
    )


def test_final_native_observation_preserves_original_failure_and_game_tape(tmp_path: Path) -> None:
    expected = run_resource_envelope()
    events = _history(tmp_path, expected)
    original = project_fleet_run_detail(events, lambda _: None)
    detail = project_fleet_run_detail([*events, _observed(expected, 4)], lambda _: None)

    assert detail.run == original.run
    assert detail.run.status is FleetRunStatus.FAILED
    assert detail.game_tape.status == original.game_tape.status
    assert detail.resource_observation == run_resource_observation(expected)
    assert detail.game_tape.resource_observation == detail.resource_observation
    assert detail.execution == original.execution


@pytest.mark.parametrize("length", (1, 2), ids=("queued", "running"))
def test_final_resource_observation_requires_terminal_run(tmp_path: Path, length: int) -> None:
    expected = run_resource_envelope()
    events = _history(tmp_path, expected)
    assert project_fleet_run_detail([*events, _observed(expected, 4)], lambda _: None)

    with pytest.raises(InvalidRunTransition, match="terminal Run"):
        project_fleet_run_detail(
            [*events[:length], _observed(expected, length + 1)], lambda _: None
        )


@pytest.mark.parametrize("field", ("fleet_run_id", "unit_name", "invocation_id"))
def test_final_observation_rejects_another_native_identity(tmp_path: Path, field: str) -> None:
    expected = run_resource_envelope()
    events = _history(tmp_path, expected)
    valid = _observed(expected, 4)
    assert project_fleet_run_detail([*events, valid], lambda _: None)
    payload = valid.model_dump()
    payload["observation"][field] = {
        "fleet_run_id": "another-run",
        "unit_name": "oh-goon-run-" + "d" * 32 + ".scope",
        "invocation_id": "e" * 32,
    }[field]

    with pytest.raises((ValueError, InvalidRunTransition), match="(same Run|original admission)"):
        changed = RunResourcesObservedEvent.model_validate(payload)
        project_fleet_run_detail([*events, changed], lambda _: None)


def test_final_resource_observation_is_recorded_at_most_once(tmp_path: Path) -> None:
    expected = run_resource_envelope()
    events = [*_history(tmp_path, expected), _observed(expected, 4)]
    assert project_fleet_run_detail(events, lambda _: None)

    with pytest.raises(InvalidRunTransition, match="already recorded"):
        project_fleet_run_detail([*events, _observed(expected, 5)], lambda _: None)


def test_resource_admission_cannot_be_transferred_to_another_run(tmp_path: Path) -> None:
    expected = run_resource_envelope()
    valid = _history(tmp_path, expected)[0]
    assert RunAdmittedEvent.model_validate(valid.model_dump()) == valid
    payload = valid.model_dump()
    payload["fleet_run_id"] = "another-run"

    with pytest.raises(ValueError, match="same Fleet run"):
        RunAdmittedEvent.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("cpu_max", "max 100000"),
        ("cpu_max", "100001 100000"),
        ("cpu_max", "100000 0"),
        ("memory_max_bytes", 64 * 1024 * 1024 + 1),
        ("memory_swap_max_bytes", 1),
        ("memory_oom_group", False),
        ("oom_policy", None),
        ("pids_max", 17),
        ("invocation_id", "0" * 32),
    ),
)
def test_native_evidence_requires_actual_selected_ceilings(field: str, value: object) -> None:
    valid = run_resource_envelope()
    payload = valid.model_dump()
    assert RunResourceEnvelopeEvidence.model_validate(payload) == valid
    payload[field] = value

    with pytest.raises(ValueError):
        RunResourceEnvelopeEvidence.model_validate(payload)
