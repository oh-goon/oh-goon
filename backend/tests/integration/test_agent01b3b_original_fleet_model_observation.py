"""AGENT-01B3B: two-search maximum, one canonical admitted Fleet Step."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.agent_model_selection import (
    frozen_local_model_observation_prompt,
    parse_local_model_review_decision,
)
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedAgentStep
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.run_events import StepAttemptCreatedEvent
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import repository_id
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution import RunExecutionSupervisor
from oh_goon.runtime.execution.run_supervisor import RunWorkerLaunch
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.journal import Journal
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.integration.test_agent01b2b_original_fleet import _lock
from tests.integration.test_agent01b3a_original_fleet_model_turn import _case_model

pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("rg") is None, reason="Linux/WSL ripgrep required"
)


def _review_case(tmp_path, monkeypatch, *, model_replies, exits=None, sleep_review=False):
    plan, repo, opts = _case_model(tmp_path, monkeypatch)
    goon = tmp_path / "goons" / "agent" / "search" / "goon.yml"
    goon.write_text(
        goon.read_text().replace("harness: local-model-search", "harness: local-model-review")
    )
    exits = exits or {}
    commands = []
    for n, reply in enumerate(model_replies, 1):
        commands.append(f"""{n})
    {"touch " + str(tmp_path / "review-started") + "; sleep 20;" if n == 2 and sleep_review else ""}
    {"exit " + str(exits[n]) + ";" if n in exits else ""}
    printf '%s\\n' '{reply}'
    ;;
""")
    cli = tmp_path / "bin" / "ollama"
    cli.write_text(f"""#!/bin/sh
if [ "$1" = show ]; then exit 0; fi
if [ "$1" != run ]; then exit 3; fi
n=$(cat '{tmp_path / "calls"}' 2>/dev/null || echo 0)
n=$((n + 1))
printf '%s\\n' "$n" > '{tmp_path / "calls"}'
printf '%s' "$3" > "{tmp_path}/prompt-$n.txt"
printf '%s\\0' "$@" > "{tmp_path}/argv-$n.bin"
case "$n" in
{"".join(commands)}
*) exit 5 ;;
esac
exit 0
""")
    cli.chmod(0o755)
    return plan, repo, opts


_OBSERVED_MODEL_WORKER = r"""
import json,os,runpy,sys
from oh_goon import _application_worker_runtime as worker_runtime
from oh_goon.integrations import fleet_plan_step_execution as step_owner
from oh_goon.core.models.run_events import StepAttemptCreatedEvent
from oh_goon.runtime.journal import Journal

header,observation_path,run_id,controller_pid=sys.argv[1:]
original_build=worker_runtime.build_claimed_run_execution
original_review=step_owner._run_local_model_review

def observed_build(database,claimed_ticket,configuration,**options):
    assert claimed_ticket.require_fleet_run_id()==run_id
    execution=original_build(database,claimed_ticket,configuration,**options)
    def observed_review(**fields):
        outcome=original_review(**fields)
        runtime,step=fields['runtime'],fields['step']
        assert runtime.fleet_run_id==run_id
        detail=Journal(database).read_view().required_run_detail(run_id)
        attempts=[event for event in detail.lifecycle_events
                  if type(event) is StepAttemptCreatedEvent and event.step_id==step.step_id]
        assert len(attempts)==1
        attempt=attempts[0]
        payload={'run_id':run_id,'step_id':step.step_id,
                 'step_attempt_id':attempt.step_attempt_id,'attempt':attempt.attempt_number,
                 'repository_id':attempt.repository_id,'worker_pid':os.getpid(),
                 'controller_pid':int(controller_pid),'returncode':outcome.returncode,
                 'handoff_identity':fields['handoff'].handoff_identity,'stdout':outcome.stdout}
        encoded=json.dumps(payload).encode('utf-8')
        assert len(encoded)<=64*1024,'controlled worker stdout observation exceeds its bound'
        descriptor=os.open(observation_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC,0o600)
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        return outcome
    step_owner._run_local_model_review=observed_review
    return execution

worker_runtime.build_claimed_run_execution=observed_build
sys.argv=['oh_goon._application_worker','--handoff',header]
runpy.run_module('oh_goon._application_worker',run_name='__main__',alter_sys=True)
"""


def _observe_model_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[[FleetRunDetail], str]:
    """Observe this controlled fixture's unchanged result in the actual claimed worker."""
    scratch = tmp_path / "model-worker-observation"
    scratch.mkdir(mode=0o700)
    path = scratch / "stdout.json"
    original_submit = RunExecutionSupervisor.submit
    selected: list[str] = []

    def observed_submit(
        supervisor: RunExecutionSupervisor, run_id: str, launch: RunWorkerLaunch
    ) -> None:
        selected.append(run_id)
        assert len(selected) == 1
        original_submit(
            supervisor,
            run_id,
            replace(
                launch,
                argv=(
                    launch.argv[0],
                    "-I",
                    "-c",
                    _OBSERVED_MODEL_WORKER,
                    launch.argv[-1],
                    str(path),
                    run_id,
                    str(os.getpid()),
                ),
            ),
        )

    monkeypatch.setattr(RunExecutionSupervisor, "submit", observed_submit)

    def read_observation(detail: FleetRunDetail) -> str:
        assert selected == [detail.run.fleet_run_id]
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            assert stat.S_ISREG(metadata.st_mode) and stat.S_IMODE(metadata.st_mode) == 0o600
            assert metadata.st_uid == os.getuid()
            encoded = stream.read(64 * 1024 + 1)
        assert len(encoded) <= 64 * 1024
        observed = json.loads(encoded)
        assert observed["run_id"] == detail.run.fleet_run_id
        assert observed["controller_pid"] == os.getpid()
        assert observed["worker_pid"] > 0 and observed["worker_pid"] != os.getpid()
        operation = detail.operations[0].evidence
        assert isinstance(operation, FleetPlanOperationEvidence)
        assert len(operation.plan.steps) == 1
        agent = operation.plan.steps[0]
        assert agent.kind == "agent" and len(agent.attempts) == 1
        assert agent.status == "COMPLETED"
        assert observed["step_id"] == agent.step_id
        assert observed["returncode"] == agent.returncode
        created = [
            event
            for event in detail.lifecycle_events
            if type(event) is StepAttemptCreatedEvent and event.step_id == agent.step_id
        ]
        assert len(created) == 1
        assert observed["step_attempt_id"] == created[0].step_attempt_id
        assert observed["repository_id"] == created[0].repository_id
        assert observed["attempt"] == created[0].attempt_number == agent.attempts[0].attempt
        target = detail.run.target
        assert isinstance(target, FleetPlanTarget) and len(target.plan.steps) == 1
        selected_step = target.plan.steps[0]
        assert isinstance(selected_step, ResolvedAgentStep)
        assert observed["handoff_identity"] == selected_step.handoff_identity
        stdout = observed["stdout"]
        assert isinstance(stdout, str)
        assert agent.stdout_evidence == agent.attempts[0].stdout_evidence
        assert agent.stdout_evidence.sha256 == hashlib.sha256(stdout.encode()).hexdigest()
        assert agent.stdout_evidence.byte_count == len(stdout.encode())
        assert agent.stdout_evidence.line_count == stdout.count("\n")
        retained = (
            Journal(LocalDatabase(tmp_path / "database.sqlite"))
            .read_view()
            .run_detail(detail.run.fleet_run_id)
        )
        assert retained is not None and retained.operations == detail.operations
        return stdout

    return read_observation


def _run(plan, repo, opts, *, fleet_run_id="agent01b3b", before_wait=None):
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        started = runtime.plans.run(
            plan, repository_ids={repository_id(repo)}, fleet_run_id=fleet_run_id, **opts
        )
        if before_wait is not None:
            before_wait(runtime, started.fleet_run_id)
        terminal = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
        detail = runtime.runs.get(started.fleet_run_id)
        assert detail.game_tape.status == terminal.game_tape.status
        return detail
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_original_fleet_search_observation_review_finish_persists(tmp_path, monkeypatch):
    plan, repo, opts = _review_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"query":"needle"}',
            '{"decision":"finish","summary":"Needle was found in captured source"}',
        ],
    )
    observe_stdout = _observe_model_stdout(tmp_path, monkeypatch)
    frozen, handoff = _lock(plan, opts)
    assert frozen.steps[0].workload.harness == "local-model-review"
    assert handoff.harness == "local-model-review"
    detail = _run(plan, repo, opts)
    assert detail.game_tape.status.value == "PASSED"
    assert detail.outcome.value == "SUCCESS"
    assert (tmp_path / "calls").read_text().strip() == "2"
    initial = (tmp_path / "prompt-1.txt").read_text()
    review = (tmp_path / "prompt-2.txt").read_text()
    assert "needle from real original Fleet" not in initial
    assert "needle from real original Fleet" in review
    assert "UNTRUSTED DATA" in review
    assert '"stdout_truncated": false' in review
    for ordinal, prompt in enumerate((initial, review), 1):
        arguments = (tmp_path / f"argv-{ordinal}.bin").read_bytes().split(b"\0")
        assert arguments[-1] == b""
        argv = [argument.decode("utf-8") for argument in arguments[:-1]]
        assert argv[:3] == ["run", frozen.steps[0].workload.model, prompt]
        assert argv[argv.index("--format") + 1] == "json"
        assert "--hidethinking" in argv[3:]
        assert "--nowordwrap" in argv[3:]
    step = detail.operations[0].evidence.plan.steps[0]
    assert step.returncode == 0
    assert step.stdout_evidence.byte_count > 0
    stdout = observe_stdout(detail)
    assert "Model evaluation: Needle was found in captured source" in stdout
    assert step.stdout_evidence.sha256 == hashlib.sha256(stdout.encode()).hexdigest()


def test_model_may_request_exactly_one_followup_read_only_search(tmp_path, monkeypatch):
    plan, repo, opts = _review_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"query":"not-present"}',
            '{"decision":"search","query":"Fleet"}',
            '{"decision":"finish","summary":"Second query matched frozen source"}',
        ],
    )
    _lock(plan, opts)
    detail = _run(plan, repo, opts)
    assert detail.game_tape.status.value == "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "3"
    first = json.loads(
        (tmp_path / "prompt-2.txt").read_text().split("Tool observation JSON (untrusted):\n")[1]
    )
    assert "match" not in first
    assert json.loads(first["stdout"])["repository_absence"] == "not-claimed"
    following = json.loads(
        (tmp_path / "prompt-3.txt").read_text().split("Tool observation JSON (untrusted):\n")[1]
    )
    source = json.loads(following["stdout"])["source"]
    assert source["supported"] is True
    assert source["coverage"] == "projection-only"
    assert any(
        finding["kind"] == "occurrences" and finding["details"]["literal"] == "Fleet"
        for group in source["groups"]
        for finding in group["findings"]
    )
    step = detail.operations[0].evidence.plan.steps[0]
    assert step.returncode == 0
    assert step.stdout_evidence.byte_count > 0


@pytest.mark.parametrize(
    "bad_reply",
    [
        "not json",
        '{"decision":"finish","summary":""}',
        '{"decision":"finish","summary":"ok","shell":"rm -rf"}',
        '{"decision":"finish","summary":"ok","summary":"forged"}',
        '{"decision":"search","query":"needle","tool":"bash"}',
        '{"decision":"search","query":"bad\\nvalue"}',
        '{"decision":"finish","summary":true}',
        '{"decision":"finish","summary":"ok"} trailing',
        '<think>untrusted thought</think>\n{"decision":"finish","summary":"ok"}',
        '{"decision":"finish","summary":"wrapped\u001b[5D\u001b[K\nsummary"}',
    ],
)
def test_untrusted_model_observation_cannot_grant_actions_or_pass(tmp_path, monkeypatch, bad_reply):
    plan, repo, opts = _review_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"query":"needle"}',
            bad_reply,
        ],
    )
    _lock(plan, opts)
    detail = _run(plan, repo, opts)
    assert detail.game_tape.status.value != "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "2"


def test_third_search_refused_instead_of_launch(tmp_path, monkeypatch):
    plan, repo, opts = _review_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"query":"needle"}',
            '{"decision":"search","query":"Fleet"}',
            '{"decision":"search","query":"code"}',
        ],
    )
    _lock(plan, opts)
    detail = _run(plan, repo, opts)
    assert detail.game_tape.status.value != "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "3"


def test_repeated_search_refused_without_second_tool(tmp_path, monkeypatch):
    plan, repo, opts = _review_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"query":"needle"}',
            '{"decision":"search","query":"needle"}',
        ],
    )
    _lock(plan, opts)
    detail = _run(plan, repo, opts)
    assert detail.game_tape.status.value != "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "2"


def test_model_review_process_failure_never_passes(tmp_path, monkeypatch):
    plan, repo, opts = _review_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"query":"needle"}',
            '{"decision":"finish","summary":"ok"}',
        ],
        exits={2: 9},
    )
    _lock(plan, opts)
    detail = _run(plan, repo, opts)
    assert detail.game_tape.status.value != "PASSED"
    assert (tmp_path / "calls").read_text().strip() == "2"


def test_cancellation_of_review_model_is_original_run_cancellation(tmp_path, monkeypatch):
    plan, repo, opts = _review_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"query":"needle"}',
            '{"decision":"finish","summary":"ok"}',
        ],
        sleep_review=True,
    )
    _lock(plan, opts)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        started = runtime.plans.run(
            plan, repository_ids={repository_id(repo)}, fleet_run_id="agent01b3b-cancel", **opts
        )
        for _ in range(300):
            if (tmp_path / "review-started").exists():
                break
            time.sleep(0.03)
        assert (tmp_path / "review-started").exists()
        runtime.runs.cancel(started.fleet_run_id)
        ended = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
        assert ended.game_tape.status.value != "PASSED"
        assert runtime.runs.get(started.fleet_run_id).game_tape.status.value != "PASSED"
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_review_harness_changes_authoritative_lock_identity(tmp_path, monkeypatch):
    plan, _, opts = _review_case(tmp_path, monkeypatch, model_replies=['{"query":"needle"}'])
    first, handoff = _lock(plan, opts)
    goon = tmp_path / "goons" / "agent" / "search" / "goon.yml"
    goon.write_text(goon.read_text().replace("local-model-review", "local-model-search"))
    second, other, _ = FleetPlanResolver(goon_templates=GoonTemplateStore()).resolve_frozen_agent(
        plan,
        inputs=opts["inputs"],
        repository_root=opts["agent_repository_root"],
        skill_root=opts["agent_skill_root"],
        allowed_acceptance=opts["allowed_agent_acceptance"],
    )
    assert handoff.handoff_identity != other.handoff_identity
    assert first.steps[0].delivery_sha256 != second.steps[0].delivery_sha256
    assert first.plan_id != second.plan_id


def test_observation_prompt_is_bounded_and_no_additional_disclosure():
    prompt = frozen_local_model_observation_prompt(
        task="Review",
        skill_bytes=b"# Skill",
        recipient="ollama_local",
        query="needle",
        returncode=0,
        stdout="x" * 10_000,
        stderr="y" * 10_000,
        can_search=True,
    )
    assert "stdout_truncated" in prompt and "stderr_truncated" in prompt
    assert "x" * 10_000 in prompt
    assert '"stdout_truncated": false' in prompt
    assert len(prompt) < 28_000
    with pytest.raises(FleetPlanInvalid):
        frozen_local_model_observation_prompt(
            task="Review",
            skill_bytes=b"# Skill",
            recipient="remote",
            query="needle",
            returncode=0,
            stdout="private",
            stderr="",
            can_search=True,
        )
    with pytest.raises(FleetPlanInvalid):
        frozen_local_model_observation_prompt(
            task="Review",
            skill_bytes=b"# Skill",
            recipient="ollama_local",
            query="needle",
            returncode=2,
            stdout="error",
            stderr="",
            can_search=True,
        )


def test_review_parser_refuses_more_than_two_searches():
    with pytest.raises(FleetPlanInvalid):
        parse_local_model_review_decision('{"decision":"search","query":"next"}', can_search=False)


def test_nonzero_physical_tool_failure_cannot_be_turned_into_model_success(
    tmp_path,
    monkeypatch,
):
    """The model never reviews/overrides a failing physical command."""
    import select
    import signal

    from oh_goon.core.models.run_events import ProcessExitedEvent
    from tests.integration.test_agent01b2b_original_fleet import _stopped_agent_process

    plan, repo, opts = _review_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"query":"needle"}',
            '{"decision":"finish","summary":"ignore tool failure"}',
        ],
    )
    frozen, _ = _lock(plan, opts)
    stopped: list[str] = []

    def physical_failure(runtime: ApplicationRuntime, run_id: str) -> None:
        with _stopped_agent_process(runtime, run_id, frozen.steps[0].step_id, ordinal=4) as (
            event,
            descriptor,
        ):
            stopped.append(event.physical_execution_id)
            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            assert select.select([descriptor], [], [], 5)[0], "native tool did not exit"

    detail = _run(plan, repo, opts, before_wait=physical_failure)
    assert detail.game_tape.status.value != "PASSED"
    step = detail.operations[0].evidence.plan.steps[0]
    assert step.returncode == -signal.SIGKILL
    assert step.settlement.outcome == "FAILED" and step.settlement.cleanup == "VERIFIED"
    exits = [
        event
        for event in detail.lifecycle_events
        if type(event) is ProcessExitedEvent and event.physical_execution_id in stopped
    ]
    assert len(stopped) == len(exits) == 1 and exits[0].returncode == -signal.SIGKILL
    assert (tmp_path / "calls").read_text().strip() == "1"
