"""A retained read-only Agent target through the existing Schedule/Fleet owners."""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import timedelta
from functools import partial
from pathlib import Path

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import (
    FleetContentReferenceUnavailable,
    FleetPlanInvalid,
    RunNotFound,
    StateConflict,
)
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentProvidersConfig,
    NativeAgentDefinition,
    NativeAgentProfile,
    PiSessionIntent,
    ResolvedNativeAgent,
)
from oh_goon.core.models.run_events import ProcessStartedEvent, StepAttemptCreatedEvent
from oh_goon.core.models.schedule import (
    ScheduleOccurrence,
    ScheduleOccurrenceRepositoryPage,
    ScheduleStatus,
)
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.core.product_identity import repository_id, schedule_repository_fleet_run_id
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunOutcome,
    FleetRunSource,
    GameTapeStatus,
    ScheduleExecutionStatus,
    ScheduleFailureStage,
    ScheduleRepositoryRunStatus,
)
from oh_goon.engine.git import GitService
from oh_goon.integrations.agent_native_execution import native_executable_sha256
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import native_profile_command_runner
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from oh_goon.services import schedule_timing

_PI_REVIEW = """#!/usr/bin/python3
import json
import os
import sys
from pathlib import Path

assert "--no-session" in sys.argv
assert "TEST_SCHEDULE_MODEL_KEY" not in os.environ
prompt = json.load(sys.stdin)
assert "Hashmarks" in prompt["repository_evidence"]
assert prompt["context"] == {}
try:
    Path("unexpected-write").write_text("review must be read-only")
except OSError:
    pass
else:
    raise AssertionError("review workspace accepted a write")
identity = prompt["identity"]
record = {
    "schema": "oh-goon.agent-review/v1",
    **identity,
    "verdict": "CLEAN",
    "summary": "Reviewed the captured source",
    "reviewed_paths": {"complete_scope": identity["scope_identity"]},
    "findings": [],
}
for event in (
    {"type": "agent_start"},
    {"type": "message_end", "message": {
        "role": "assistant", "content": json.dumps(record), "stopReason": "stop",
    }},
    {"type": "agent_settled", "aborted": False},
):
    print(json.dumps(event), flush=True)
"""


@pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bwrap") is None, reason="Bubblewrap required"
)
def test_retained_ephemeral_agent_runs_as_two_distinct_schedule_occurrences(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repositories" / "sample"
    repository.mkdir(parents=True)
    source = repository / "source.py"
    source.write_text("VALUE = 1\n")
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    skill = tmp_path / "skills" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Review\nReview the captured source.\n")
    executable = tmp_path / "pi-fixture"
    executable.write_text(_PI_REVIEW)
    executable.chmod(0o700)
    database = tmp_path / "state" / "app.sqlite"
    monkeypatch.setenv("OH_GOON_DB", str(database))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(tmp_path / "league.yml"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("HASHMARKS_NO_UPDATE_CHECK", "1")
    credential = "schedule-fixture-secret-value"
    monkeypatch.setenv("TEST_SCHEDULE_MODEL_KEY", credential)
    isolation = BubblewrapProcessIsolation(shutil.which("bwrap") or "")
    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="scheduled-review",
        definition=NativeAgentDefinition(executable=str(executable)),
        profile=NativeAgentProfile(
            backend="bubblewrap",
            inference_profile="fixture",
            timeout_seconds=30.0,
        ),
        executable_sha256=native_executable_sha256(executable),
        connection=AgentConnection.select(
            AgentProvidersConfig.model_validate(
                {
                    "providers": {
                        "fixture": {
                            "api": "openai-chat-completions",
                            "base_url": "http://127.0.0.1:1/v1",
                            "api_key_env": "TEST_SCHEDULE_MODEL_KEY",
                        }
                    },
                    "models": {"fixture": {"provider": "fixture", "model": "fixture"}},
                }
            ),
            "fixture",
        ),
        backend_configuration_id=isolation.configuration_identity(),
        session=PiSessionIntent(mode="ephemeral"),
    )
    git = native_profile_command_runner(
        repository,
        selected,
        isolation,
        RepositoryWorkspaceAuthority.pin_repository_subject,
    )

    def scoped_runtime() -> ApplicationRuntime:
        return ApplicationRuntime.build(
            standard_local=True,
            standard_process_isolation=isolation,
            native_git_service=GitService(
                command_runner=git,
                observation_runner=partial(git, read_only_workspace=True),
            ),
        )

    runtime = scoped_runtime()
    try:
        runtime.locations.add(repository)
        owner = repository_id(repository)
        seed = runtime.plans.run_native_agent(
            selected=selected,
            workspace=repository,
            skill=skill,
            task="Review the sample source",
            role="review",
        )
        completed = runtime.runs.wait_for_terminal(seed.fleet_run_id, timeout=45)
        assert completed.outcome is FleetRunOutcome.SUCCESS, completed.game_tape.model_dump_json()
        target = completed.run.target
        assert isinstance(target, FleetPlanTarget) and target.agent_deliveries
        source.write_text("VALUE = 2\n")
        with pytest.raises(StateConflict, match="admission became stale"):
            runtime.plans.schedule_retained_review(
                seed.fleet_run_id,
                schedule_id="stale-review",
                interval_minutes=1,
            )
        assert runtime.schedules.catalog().schedules == ()
        source.write_text("VALUE = 1\n")

        unscoped = ApplicationRuntime.build(standard_local=True, startup_purpose="workflow")
        try:
            with pytest.raises(FleetPlanInvalid, match="scoped Git execution boundary"):
                unscoped.plans.schedule_retained_review(
                    seed.fleet_run_id,
                    schedule_id="unscoped-review",
                    interval_minutes=1,
                )
            assert unscoped.schedules.catalog().schedules == ()
        finally:
            unscoped.shutdown(wait_for_runs=True)

        definition = runtime.plans.schedule_retained_review(
            seed.fleet_run_id,
            schedule_id="native-review",
            interval_minutes=1,
            name="Native review",
        )
        (admission,) = completed.repository_admissions
        assert definition.target.plan == target.plan
        assert definition.target.agent_deliveries == target.agent_deliveries
        assert definition.target.reviewed_repository_source == admission.source_authority
        assert definition.target.retained_review_run_id == seed.fleet_run_id
        assert credential not in definition.model_dump_json()
        assert "TEST_SCHEDULE_MODEL_KEY" in definition.model_dump_json()

        def status() -> ScheduleStatus:
            (current,) = runtime.schedules.catalog().schedules
            return current

        def require_no_child(
            reason: str,
        ) -> tuple[ScheduleOccurrence, ScheduleOccurrenceRepositoryPage]:
            occurrence = status().latest_occurrence
            assert occurrence is not None
            assert occurrence.status is ScheduleExecutionStatus.FAILED
            page = runtime.schedules.occurrence_repositories(
                definition.schedule_id,
                occurrence.schedule_run_id,
            )
            (child,) = page.items
            assert child.status is ScheduleRepositoryRunStatus.NOT_STARTED
            assert child.fleet_run_id is None
            assert child.failure is not None
            assert child.failure.stage is ScheduleFailureStage.ADMISSION
            assert reason in child.failure.message
            with pytest.raises(RunNotFound):
                runtime.runs.get(
                    schedule_repository_fleet_run_id(occurrence.schedule_run_id, owner)
                )
            return occurrence, page

        runtime.shutdown(wait_for_runs=True)
        runtime = ApplicationRuntime.build(standard_local=True)
        first_due = definition.created_at + timedelta(minutes=1)
        now = first_due - timedelta(microseconds=1)
        monkeypatch.setattr(schedule_timing, "utc_now", lambda: now)
        runtime.schedules.tick()
        assert status().latest_occurrence is None
        now = first_due
        runtime.schedules.tick()
        refused_occurrence, refused_page = require_no_child(
            "original scoped Git and isolation binding"
        )
        assert refused_occurrence.finished_at is not None

        runtime.shutdown(wait_for_runs=True)
        runtime = scoped_runtime()

        # An ordinary Plan on another registered repository keeps its own Git
        # runtime even while this process is selected for native review.
        ordinary = tmp_path / "repositories" / "ordinary"
        ordinary.mkdir()
        subprocess.run(["git", "init", "-q", str(ordinary)], check=True)
        (ordinary / "check.sh").write_text("printf 'ordinary verification\\n'\n")
        runtime.locations.add(ordinary)
        workflow = tmp_path / "ordinary-workflow"
        goon = workflow / "goons" / "general" / "check"
        goon.mkdir(parents=True)
        (goon / "goon.yml").write_text("steps:\n- run: sh check.sh\n")
        (goon / "README.md").write_text("# general/check\n\nRun the ordinary check.\n")
        plan = workflow / "plan.yml"
        plan.write_text("goons:\n- import: general/check\n  from: ./goons\n")
        runtime.plans.lock(plan)
        ordinary_run = runtime.plans.run(plan, repository_ids={repository_id(ordinary)})
        verified = runtime.runs.wait_for_terminal(ordinary_run.fleet_run_id, timeout=45)
        assert verified.outcome is FleetRunOutcome.SUCCESS, verified.game_tape.model_dump_json()

        baseline = refused_occurrence.finished_at
        run_ids = {seed.fleet_run_id}
        process_ids: set[str] = set()
        for _ in range(2):
            before = status()
            due = baseline + timedelta(minutes=1)
            assert before.next_run_at == due
            now = due - timedelta(microseconds=1)
            runtime.schedules.tick()
            assert status().latest_occurrence == before.latest_occurrence

            now = due
            runtime.schedules.tick()
            current = status()
            occurrence = current.latest_occurrence
            assert occurrence is not None and occurrence.status is ScheduleExecutionStatus.RUNNING
            assert current.next_run_at is None
            child = runtime.schedules.occurrence_repositories(
                definition.schedule_id,
                occurrence.schedule_run_id,
            )
            run_id = schedule_repository_fleet_run_id(occurrence.schedule_run_id, owner)
            assert tuple(item.fleet_run_id for item in child.items) == (run_id,)
            assert run_id not in run_ids
            run_ids.add(run_id)

            detail = runtime.runs.wait_for_terminal(run_id, timeout=45)
            assert detail.outcome is FleetRunOutcome.SUCCESS, detail.game_tape.model_dump_json()
            assert detail.run.source is FleetRunSource.SCHEDULED
            assert detail.run.target == definition.target
            assert credential not in detail.model_dump_json()
            (repository_tape,) = detail.game_tape.repositories
            (step,) = repository_tape.steps
            assert step.status is GameTapeStatus.PASSED
            (claim,) = step.native_agent_observations
            assert claim.agent == "pi" and claim.settled and not claim.failed
            events = runtime.runs.events(run_id)
            assert not events.has_more
            assert credential not in events.model_dump_json()
            (admitted,) = [
                event
                for event in events.events
                if event.event_type is FleetRunEventType.RUN_ADMITTED
            ]
            (attempt,) = [
                event for event in events.events if isinstance(event, StepAttemptCreatedEvent)
            ]
            assert attempt.attempt_number == 1
            processes = [
                event
                for event in events.events
                if isinstance(event, ProcessStartedEvent)
                and event.event_type is FleetRunEventType.PROCESS_STARTED
                and event.step_attempt_id == attempt.step_attempt_id
            ]
            current_processes = {event.physical_execution_id for event in processes}
            assert current_processes and len(current_processes) == len(processes)
            assert all(
                admitted.sequence < attempt.sequence < process.sequence for process in processes
            )
            assert process_ids.isdisjoint(current_processes)
            process_ids.update(current_processes)

            runtime.schedules.tick()
            settled = status().latest_occurrence
            assert settled is not None and settled.schedule_run_id == occurrence.schedule_run_id
            assert settled.status is ScheduleExecutionStatus.COMPLETED
            assert settled.finished_at is not None
            baseline = settled.finished_at

        assert runtime.runs.get(seed.fleet_run_id).run == completed.run
        assert not (repository / "unexpected-write").exists()
        assert (
            runtime.schedules.occurrence_repositories(
                definition.schedule_id,
                refused_occurrence.schedule_run_id,
            )
            == refused_page
        )
        store = FleetContentStore(
            ApplicationStatePathLayout.from_database(database).content_store,
            initialize=False,
        )
        (delivery,) = target.agent_deliveries
        store.release_reference(delivery.reference_id, owner_repository_id=owner)
        with pytest.raises(FleetContentReferenceUnavailable):
            runtime.plans.schedule_retained_review(
                seed.fleet_run_id,
                schedule_id="missing-review",
                interval_minutes=1,
            )
        now = baseline + timedelta(minutes=1)
        runtime.schedules.tick()
        missing_occurrence, missing_page = require_no_child(
            "retained Agent delivery is unavailable"
        )
        assert missing_occurrence.schedule_run_id != refused_occurrence.schedule_run_id
        assert delivery.reference_id not in missing_page.model_dump_json()
        assert status().definition == definition
    finally:
        runtime.shutdown(wait_for_runs=True)
