"""Native admission refusal under the original application's single-Run custody."""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from typing import Any, Literal, cast
from uuid import uuid4

import pytest

from oh_goon._application_foundation import RuntimeFoundation, build_runtime_foundation
from oh_goon._application_services import build_product_services
from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import ExecutionBusy
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentProvidersConfig,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.run import FleetRunDetail, FleetRunState
from oh_goon.core.models.run_events import FleetRunEvent, ProcessStartedEvent, ProcessStartingEvent
from oh_goon.core.process_birth import ProcessBirthIdentity, observe_process_birth
from oh_goon.core.vocabulary import FleetRunEventType, FleetRunOutcome, FleetRunStatus
from oh_goon.engine.atomic_execution_bytes import atomic_execution_bytes_supported
from oh_goon.engine.git import GitService
from oh_goon.integrations.agent_native_execution import native_executable_sha256
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import native_profile_command_runner
from oh_goon.integrations.hashmarks import require_loaded_hashmarks_runtime
from oh_goon.repository_tooling.process_metrics import read_linux_process_sample
from oh_goon.runtime.execution import ExecutionLease
from oh_goon.runtime.execution.run_supervisor import RunExecutionSupervisor, RunWorkerLaunch
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority

_Agent = Literal["pi", "opencode"]


@dataclass(frozen=True)
class _NativeAdmissionCase:
    runtimes: tuple[ApplicationRuntime, ...]
    foundations: tuple[RuntimeFoundation, ...]
    selected: tuple[ResolvedNativeAgent, ...]
    repositories: tuple[Path, ...]
    skills: tuple[Path, ...]
    tasks: tuple[str, ...]
    skill_texts: tuple[str, ...]
    requests: dict[str, list[dict[str, Any]]]
    prompts: dict[str, dict[str, Any]]
    errors: list[str]
    release: Event
    a_response_finished: Event
    admitted_a: FleetRunState
    run_id_b: str
    launched_run_ids: list[str]
    a_lease: ExecutionLease
    worker_fd: int
    worker_birth: ProcessBirthIdentity

    def start_b(self) -> FleetRunState:
        return self.runtimes[1].plans.run_native_agent(
            selected=self.selected[1],
            workspace=self.repositories[1],
            skill=self.skills[1],
            task=self.tasks[1],
            role="review",
            verification=(("/usr/bin/python3", "verify.py"),),
            fleet_run_id=self.run_id_b,
        )


def _controlled_program(path: Path, agent: _Agent) -> None:
    path.write_text(
        "#!/usr/bin/python3\n"
        "import json,os,pathlib,sys,urllib.request\n"
        f"agent={agent!r}\n"
        "assert not any(key.startswith('HUG122_KEY_') for key in os.environ)\n"
        "prompt=json.load(sys.stdin)\n"
        "for phase in ('initial request','followup without task history'):\n"
        "    body=json.dumps({'model':'selected','metadata':{'fixture_prompt_file':str(pathlib.Path(prompt['complete_source_manifest']).with_name('prompt.txt'))},'messages':[{'role':'user','content':phase}]}).encode()\n"
        "    request=urllib.request.Request('http://127.0.0.1:18743/v1/chat/completions',data=body,headers={'Content-Type':'application/json'})\n"
        "    with urllib.request.urlopen(request,timeout=90) as response: assert json.load(response)['choices']\n"
        "record={'schema':'oh-goon.agent-review/v1',**prompt['identity'],'verdict':'CLEAN','summary':'Genuine review: '+prompt['task'],'reviewed_paths':{'complete_scope':prompt['identity']['scope_identity']},'findings':[]}\n"
        "text=json.dumps(record)\n"
        "if agent=='pi': events=[{'type':'message_end','message':{'role':'assistant','content':text,'stopReason':'stop'}},{'type':'agent_settled','aborted':False}]\n"
        "else: events=[{'type':'step_start'},{'type':'text','part':{'text':text,'time':{'end':1}}},{'type':'step_finish'}]\n"
        "for event in events: print(json.dumps(event),flush=True)\n"
    )
    path.chmod(0o700)


@contextmanager
def _native_admission_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    agents: tuple[_Agent, _Agent],
    same_profile: bool = False,
    installed: bool = False,
) -> Iterator[_NativeAdmissionCase]:
    repositories = (tmp_path / "repos/a", tmp_path / "repos/b")
    skills = (tmp_path / "skills/a", tmp_path / "skills/b")
    tasks = ("Review phase A; retain TASK_A_122.", "Review phase B; retain TASK_B_122.")
    skill_texts = ("# Phase A\nRetain SKILL_A_122.\n", "# Phase B\nRetain SKILL_B_122.\n")
    for index, (repo, skill) in enumerate(zip(repositories, skills, strict=True)):
        repo.mkdir(parents=True)
        (repo / "source.py").write_text(f"VALUE = {42 + index}\n")
        (repo / "verify.py").write_text(f"from source import VALUE\nassert VALUE == {42 + index}\n")
        subprocess.run(("git", "init", "-q", str(repo)), check=True, timeout=10)
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(skill_texts[index])
    for key, value in (
        ("OH_GOON_DB", tmp_path / "state/app.sqlite"),
        ("XDG_CONFIG_HOME", tmp_path / "config"),
        ("XDG_DATA_HOME", tmp_path / "data"),
        ("XDG_STATE_HOME", tmp_path / "state"),
        ("HOME", tmp_path / "synthetic-home"),
    ):
        monkeypatch.setenv(key, str(value))
    monkeypatch.delenv("OH_GOON_EXECUTORS_FILE", raising=False)
    monkeypatch.setenv("HUG122_KEY_A", "synthetic-a-key")
    monkeypatch.setenv("HUG122_KEY_B", "synthetic-b-key")
    for key in ("OPENAI_API_KEY", "CODEX_HOME", "PI_CODING_AGENT_DIR", "OPENCODE_CONFIG_CONTENT"):
        monkeypatch.delenv(key, raising=False)
    definitions: dict[str, NativeAgentDefinition] = {}
    for agent in dict.fromkeys(agents):
        if installed:
            executable = os.environ.get(f"OH_GOON_NATIVE_{agent.upper()}")
            native_root = os.environ.get(f"OH_GOON_NATIVE_{agent.upper()}_RUNTIME_ROOT")
            if not executable or not native_root:
                pytest.skip(f"Select installed {agent} and its public runtime root")
            program = Path(executable).resolve(strict=True)
            public_root = Path(native_root).resolve(strict=True)
            assert public_root.is_dir() and program.is_relative_to(public_root)
            definitions[agent] = NativeAgentDefinition(
                executable=str(program), runtime_roots=(str(public_root),)
            )
        else:
            program = tmp_path / f"native-{agent}"
            _controlled_program(program, agent)
            definitions[agent] = NativeAgentDefinition(executable=str(program))

    arrived, release, a_response_finished = Event(), Event(), Event()
    requests: dict[str, list[dict[str, Any]]] = {"a": [], "b": []}
    prompts: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    with tempfile.TemporaryDirectory(prefix="og122-", dir="/tmp") as scratch:
        scratch_root = Path(scratch)
        monkeypatch.setenv("TMPDIR", scratch)

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                label: str | None = None
                try:
                    size = int(self.headers["Content-Length"])
                    assert 0 < size <= 1_048_576
                    body = json.loads(self.rfile.read(size))
                    assert self.path == "/v1/chat/completions"
                    assert body["model"] == "selected"
                    prompt: dict[str, Any] | None = None
                    metadata = body.get("metadata", {})
                    if "fixture_prompt_file" in metadata:
                        path = Path(metadata["fixture_prompt_file"])
                        assert path.is_relative_to(scratch_root) and path.name == "prompt.txt"
                        with path.open("rb") as stream:
                            encoded = stream.read(1_048_577)
                        assert len(encoded) <= 1_048_576
                        prompt = json.loads(encoded)
                    else:
                        for message in body["messages"]:
                            if message.get("role") != "user":
                                continue
                            content = message.get("content", [])
                            texts = (
                                [content]
                                if isinstance(content, str)
                                else [
                                    part["text"] for part in content if part.get("type") == "text"
                                ]
                            )
                            for text in texts:
                                offset = text.find('{"source_authority":')
                                if offset >= 0:
                                    candidate, _ = json.JSONDecoder().raw_decode(text[offset:])
                                    if candidate.get("task") in tasks:
                                        prompt = candidate
                    assert prompt is not None and prompt["task"] in tasks
                    label = "a" if prompt["task"] == tasks[0] else "b"
                    index = 0 if label == "a" else 1
                    assert len(requests[label]) < 6
                    assert self.headers["Authorization"] == (
                        "Bearer synthetic-a-key"
                        if same_profile or label == "a"
                        else "Bearer synthetic-b-key"
                    )
                    if label in prompts:
                        assert prompts[label] == prompt
                    else:
                        prompts[label] = prompt
                    requests[label].append(body)
                    if label == "a" and not arrived.is_set():
                        arrived.set()
                        assert release.wait(60), "held original A model request was not released"
                    record = {
                        "schema": "oh-goon.agent-review/v1",
                        **prompt["identity"],
                        "verdict": "CLEAN",
                        "summary": "Genuine review: " + tasks[index],
                        "reviewed_paths": {"complete_scope": prompt["identity"]["scope_identity"]},
                        "findings": [],
                    }
                    text = json.dumps(record)
                    if body.get("stream"):
                        frames = [
                            {
                                "id": "fixture",
                                "object": "chat.completion.chunk",
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {"role": "assistant", "content": text},
                                        "finish_reason": None,
                                    }
                                ],
                            },
                            {
                                "id": "fixture",
                                "object": "chat.completion.chunk",
                                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                            },
                        ]
                        payload = (
                            b"".join(
                                b"data: " + json.dumps(frame).encode() + b"\n\n" for frame in frames
                            )
                            + b"data: [DONE]\n\n"
                        )
                    else:
                        payload = json.dumps(
                            {
                                "choices": [
                                    {
                                        "message": {"role": "assistant", "content": text},
                                        "finish_reason": "stop",
                                    }
                                ]
                            }
                        ).encode()
                    self.send_response(200)
                    self.send_header(
                        "Content-Type",
                        "text/event-stream" if body.get("stream") else "application/json",
                    )
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError) as error:
                    # An original cancelled A request may have no remaining peer.
                    if label != "a":
                        errors.append(f"Unexpected {type(error).__name__} in B model response")
                except Exception as error:
                    errors.append(f"{type(error).__name__}: {error}")
                    self.send_error(500)
                finally:
                    if label == "a":
                        a_response_finished.set()

            def log_message(self, *_args: object) -> None:
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            server_thread = Thread(target=server.serve_forever)
            server_thread.start()
            runtimes: list[ApplicationRuntime] = []
            worker_fd: int | None = None
            with ExitStack() as cleanup:

                def join_server() -> None:
                    server_thread.join(timeout=5)
                    assert not server_thread.is_alive()

                def close_worker() -> None:
                    if worker_fd is not None:
                        os.close(worker_fd)

                cleanup.callback(join_server)
                cleanup.callback(server.shutdown)
                cleanup.callback(close_worker)
                try:
                    backend = BubblewrapProcessIsolation(shutil.which("bwrap") or "")
                    catalog = AgentProvidersConfig.model_validate(
                        {
                            "providers": {
                                label: {
                                    "api": "openai-chat-completions",
                                    "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                                    "api_key_env": "HUG122_KEY_" + label.upper(),
                                }
                                for label in ("a", "b")
                            },
                            "models": {
                                label: {"provider": label, "model": "selected"}
                                for label in ("a", "b")
                            },
                        }
                    )
                    selections = tuple(
                        ResolvedNativeAgent(
                            agent=agent,
                            profile_name="shared" if same_profile else f"profile-{label}",
                            definition=definitions[agent],
                            profile=NativeAgentProfile(
                                backend="bubblewrap",
                                inference_profile="a" if same_profile else label,
                                timeout_seconds=90.0,
                            ),
                            executable_sha256=native_executable_sha256(
                                Path(definitions[agent].executable or "")
                            ),
                            connection=AgentConnection.select(
                                catalog, "a" if same_profile else label
                            ),
                            backend_configuration_id=backend.configuration_identity(),
                        )
                        for label, agent in zip(("a", "b"), agents, strict=True)
                    )
                    if same_profile:
                        assert selections[0] == selections[1]
                    foundations: list[RuntimeFoundation] = []
                    for repo, selected in zip(repositories, selections, strict=True):
                        runner = native_profile_command_runner(
                            repo,
                            selected,
                            backend,
                            RepositoryWorkspaceAuthority.pin_repository_subject,
                        )
                        foundation = build_runtime_foundation(
                            standard_local=True,
                            standard_process_isolation=backend,
                            git_service=GitService(
                                command_runner=runner,
                                observation_runner=partial(runner, read_only_workspace=True),
                            ),
                        )
                        foundations.append(foundation)
                        runtimes.append(build_product_services(foundation))
                        cleanup.callback(
                            runtimes[-1].shutdown,
                            wait_for_runs=False,
                            cancel_grace_seconds=1,
                            containment_timeout_seconds=5,
                        )
                    runtimes[0].locations.add(repositories[0].parent)
                    launched_run_ids: list[str] = []
                    original_submit = RunExecutionSupervisor.submit

                    def submit(
                        supervisor: RunExecutionSupervisor, run_id: str, launch: RunWorkerLaunch
                    ) -> None:
                        launched_run_ids.append(run_id)
                        original_submit(supervisor, run_id, launch)

                    monkeypatch.setattr(RunExecutionSupervisor, "submit", submit)
                    suffix = uuid4().hex
                    admitted_a = runtimes[0].plans.run_native_agent(
                        selected=selections[0],
                        workspace=repositories[0],
                        skill=skills[0],
                        task=tasks[0],
                        role="review",
                        verification=(("/usr/bin/python3", "verify.py"),),
                        fleet_run_id="native-a-" + suffix,
                    )
                    assert arrived.wait(45), ("original A worker did not reach provider", errors)
                    lease = foundations[0].execution.active_lease()
                    assert lease is not None and lease.custody is not None
                    assert lease.custody.phase == "WORKER_OWNED"
                    worker_fd = os.pidfd_open(lease.custody.owner_pid)
                    birth = observe_process_birth(lease.custody.owner_pid)
                    assert birth is not None and birth.pid != os.getpid()
                    assert (
                        lease.custody.owner_identity
                        == f"{birth.provider}:{birth.boot_id}:{birth.start_ticks}"
                    )
                    assert not select.select([worker_fd], [], [], 0)[0]
                    yield _NativeAdmissionCase(
                        tuple(runtimes),
                        tuple(foundations),
                        selections,
                        repositories,
                        skills,
                        tasks,
                        skill_texts,
                        requests,
                        prompts,
                        errors,
                        release,
                        a_response_finished,
                        admitted_a,
                        "native-b-" + suffix,
                        launched_run_ids,
                        lease,
                        worker_fd,
                        birth,
                    )
                finally:
                    release.set()


def _assert_context(case: _NativeAdmissionCase, label: str) -> None:
    index = 0 if label == "a" else 1
    prompt = case.prompts[label]
    assert prompt["task"] == case.tasks[index]
    assert prompt["skill_instructions"] == case.skill_texts[index]
    assert prompt["context"] == {}
    packet = prompt["repository_evidence"]
    evidence = json.loads(packet)
    assert evidence["producer_implementation_identity"] == require_loaded_hashmarks_runtime()
    assert evidence["capture_identity"] == prompt["identity"]["candidate_identity"]
    assert case.requests[label]
    for body in case.requests[label]:
        privileged = "\n".join(
            message["content"]
            for message in body["messages"]
            if message["role"] in {"system", "developer"}
        )
        assert packet in privileged and case.skill_texts[index] in privileged
        assert case.tasks[index] in privileged.replace(packet, "").replace(
            case.skill_texts[index], ""
        )
        assert case.tasks[1 - index] not in privileged
        assert case.skill_texts[1 - index] not in privileged


def _assert_physical_partition(detail: FleetRunDetail) -> None:
    events = cast(tuple[FleetRunEvent, ...], detail.lifecycle_events)
    terminal = [
        event
        for event in events
        if event.event_type
        in {
            FleetRunEventType.RUN_COMPLETED,
            FleetRunEventType.RUN_FAILED,
            FleetRunEventType.RUN_CANCELLED,
        }
    ]
    assert len(terminal) == 1
    processes = [event for event in events if isinstance(event, ProcessStartingEvent)]
    starts = [event for event in processes if type(event) is ProcessStartedEvent]
    ids = {event.physical_execution_id for event in starts}
    assert ids and len(ids) == len(starts)
    for kind in (FleetRunEventType.PROCESS_EXITED, FleetRunEventType.DESCENDANTS_QUIESCENT):
        records = [event for event in processes if event.event_type is kind]
        assert (
            len(records) == len(ids) and {event.physical_execution_id for event in records} == ids
        )
        assert max(event.sequence for event in records) < terminal[0].sequence
        for record in records:
            if record.step_id is not None:
                step_terminal = [
                    event
                    for event in events
                    if event.event_type
                    in {
                        FleetRunEventType.STEP_SUCCEEDED,
                        FleetRunEventType.STEP_FAILED,
                        FleetRunEventType.STEP_CANCELLED,
                    }
                    and getattr(event, "step_id", None) == record.step_id
                ]
                assert len(step_terminal) == 1 and record.sequence < step_terminal[0].sequence


@pytest.mark.process
@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="native protected Run transport")
@pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bwrap") is None, reason="Linux Bubblewrap"
)
@pytest.mark.parametrize(
    "same_profile", (True, False), ids=("same-profile-complete", "mixed-profile-cancel")
)
def test_busy_native_run_is_refused_without_another_worker_or_model_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, same_profile: bool
) -> None:
    with _native_admission_case(
        tmp_path,
        monkeypatch,
        agents=("pi", "pi" if same_profile else "opencode"),
        same_profile=same_profile,
    ) as case:
        runtime_a, runtime_b = case.runtimes
        before = runtime_a.runs.events(case.admitted_a.fleet_run_id).events
        a_prompt = dict(case.prompts["a"])
        with pytest.raises(ExecutionBusy, match="already active"):
            case.start_b()
        assert Journal(case.foundations[1].database).read_view().run_detail(case.run_id_b) is None
        assert case.launched_run_ids == [case.admitted_a.fleet_run_id]
        assert not case.requests["b"] and "b" not in case.prompts
        assert runtime_a.runs.events(case.admitted_a.fleet_run_id).events == before
        assert case.foundations[1].execution.active_lease() == case.a_lease
        assert observe_process_birth(case.worker_birth.pid) == case.worker_birth
        assert not select.select([case.worker_fd], [], [], 0)[0]
        assert case.prompts["a"] == a_prompt
        if same_profile:
            case.release.set()
        else:
            assert runtime_a.runs.cancel(
                case.admitted_a.fleet_run_id
            ).status.cancel_request_satisfied
        detail_a = runtime_a.runs.wait_for_terminal(case.admitted_a.fleet_run_id, timeout=45)
        assert detail_a.run.status is (
            FleetRunStatus.COMPLETED if same_profile else FleetRunStatus.CANCELLED
        )
        if same_profile:
            assert detail_a.outcome is FleetRunOutcome.SUCCESS
        _assert_physical_partition(detail_a)
        admitted_b = case.start_b()
        detail_b = runtime_b.runs.wait_for_terminal(admitted_b.fleet_run_id, timeout=45)
        assert detail_b.outcome is FleetRunOutcome.SUCCESS
        assert case.launched_run_ids == [case.admitted_a.fleet_run_id, admitted_b.fleet_run_id]
        assert not case.errors
        _assert_context(case, "a")
        _assert_context(case, "b")
        _assert_physical_partition(detail_b)
        assert case.prompts["a"]["identity"] != case.prompts["b"]["identity"]
        assert (
            case.prompts["a"]["complete_source_manifest"]
            != case.prompts["b"]["complete_source_manifest"]
        )
        assert all(step.exit_code == 0 for step in detail_b.game_tape.repositories[0].steps)
        assert detail_b.game_tape.repositories[0].steps[1].native_agent_observations == ()
        case.release.set()
        assert case.a_response_finished.wait(5)
        assert runtime_b.runs.get(admitted_b.fleet_run_id).operations == detail_b.operations
        assert runtime_b.runs.get(case.admitted_a.fleet_run_id).operations == detail_a.operations
        assert not case.errors
    reopened = ApplicationRuntime.build(standard_local=True, startup_purpose="observation")
    try:
        for detail in (detail_a, detail_b):
            retained = reopened.runs.get(detail.run.fleet_run_id)
            assert retained.run == detail.run and retained.operations == detail.operations
            assert retained.lifecycle_events == detail.lifecycle_events
    finally:
        reopened.shutdown(wait_for_runs=True)


@pytest.mark.certification
def test_installed_mixed_native_runs_refuse_overlap_and_start_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shared-database custody refuses overlap; it does not promise parallel capacity."""
    with _native_admission_case(
        tmp_path, monkeypatch, agents=("pi", "opencode"), installed=True
    ) as case:
        runtime_a, runtime_b = case.runtimes

        def sample_worker() -> dict[str, Any]:
            assert observe_process_birth(case.worker_birth.pid) == case.worker_birth
            assert not select.select([case.worker_fd], [], [], 0)[0]
            sample = read_linux_process_sample(case.worker_birth.pid)
            assert observe_process_birth(case.worker_birth.pid) == case.worker_birth
            assert not select.select([case.worker_fd], [], [], 0)[0]
            return {
                "scope": "original Run A worker process only",
                "birth": case.worker_birth.to_payload(),
                **asdict(sample),
            }

        assert case.selected[0].agent == "pi" and case.selected[1].agent == "opencode"
        assert case.selected[0] != case.selected[1]
        assert case.repositories[0] != case.repositories[1]
        assert case.foundations[0].database.path == case.foundations[1].database.path
        assert case.foundations[0].database.session_id != case.foundations[1].database.session_id
        assert case.a_lease.fleet_run_id == case.admitted_a.fleet_run_id
        assert case.a_lease.custody is not None and case.a_lease.custody.phase == "WORKER_OWNED"
        assert case.a_lease.custody.owner_pid == case.worker_birth.pid
        _assert_context(case, "a")
        samples = [sample_worker()]

        before = runtime_a.runs.events(case.admitted_a.fleet_run_id).events
        a_prompt = dict(case.prompts["a"])
        a_requests = len(case.requests["a"])
        with pytest.raises(ExecutionBusy, match="already active"):
            case.start_b()
        assert Journal(case.foundations[1].database).read_view().run_detail(case.run_id_b) is None
        assert case.launched_run_ids == [case.admitted_a.fleet_run_id]
        assert not case.requests["b"] and "b" not in case.prompts
        assert case.foundations[1].supervisor.snapshot().active_runs == 0
        assert runtime_a.runs.events(case.admitted_a.fleet_run_id).events == before
        assert case.foundations[1].execution.active_lease() == case.a_lease
        assert case.prompts["a"] == a_prompt and len(case.requests["a"]) == a_requests
        samples.append(sample_worker())

        case.release.set()
        detail_a = runtime_a.runs.wait_for_terminal(case.admitted_a.fleet_run_id, timeout=45)
        assert detail_a.outcome is FleetRunOutcome.SUCCESS
        assert detail_a.run.status is FleetRunStatus.COMPLETED
        _assert_physical_partition(detail_a)
        admitted_b = case.start_b()
        detail_b = runtime_b.runs.wait_for_terminal(admitted_b.fleet_run_id, timeout=45)
        assert detail_b.outcome is FleetRunOutcome.SUCCESS
        assert detail_b.run.status is FleetRunStatus.COMPLETED
        assert admitted_b.fleet_run_id != case.admitted_a.fleet_run_id
        assert case.launched_run_ids == [case.admitted_a.fleet_run_id, admitted_b.fleet_run_id]
        assert not case.errors, case.errors
        _assert_context(case, "a")
        _assert_context(case, "b")
        _assert_physical_partition(detail_b)
        assert case.prompts["a"]["identity"] != case.prompts["b"]["identity"]
        assert (
            case.prompts["a"]["identity"]["candidate_identity"]
            != case.prompts["b"]["identity"]["candidate_identity"]
        )
        assert (
            case.prompts["a"]["complete_source_manifest"]
            != case.prompts["b"]["complete_source_manifest"]
        )
        process_ids = []
        event_lists = [
            runtime_a.runs.events(detail_a.run.fleet_run_id).events,
            runtime_b.runs.events(detail_b.run.fleet_run_id).events,
        ]
        for detail, events in zip((detail_a, detail_b), event_lists, strict=True):
            assert detail.lifecycle_events == events
            steps = detail.game_tape.repositories[0].steps
            assert len(steps) == 2
            assert all(step.status.value == "PASSED" and step.exit_code == 0 for step in steps)
            assert len(steps[0].native_agent_observations) == 1
            claim = steps[0].native_agent_observations[0]
            assert claim.settled and not claim.failed
            assert claim.authority == "UNTRUSTED_HARNESS_CLAIM"
            assert steps[1].native_agent_observations == ()
            for step in steps:
                assert len(step.attempts) == 1
                assert step.attempts[0].supervisor is not None
                assert step.attempts[0].supervisor.final_process_group_quiescent is True
            process_ids.append(
                {
                    event.physical_execution_id
                    for event in events
                    if type(event) is ProcessStartedEvent
                }
            )
        assert process_ids[0].isdisjoint(process_ids[1])
        retained_a = runtime_b.runs.get(case.admitted_a.fleet_run_id)
        assert retained_a.run == detail_a.run
        assert retained_a.operations == detail_a.operations
        assert retained_a.lifecycle_events == detail_a.lifecycle_events
        assert case.a_response_finished.wait(5)
        caps = [selected.profile.resources.model_dump(mode="json") for selected in case.selected]
        assert all(all(value is None for value in resource.values()) for resource in caps)
        home = tmp_path / "version-only-home"
        home.mkdir(mode=0o700)
        identities = []
        for selected in case.selected:
            assert selected.definition.executable is not None
            program = Path(selected.definition.executable)
            version = subprocess.run(
                (str(program), "--version"),
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "HOME": str(home),
                    "PI_CODING_AGENT_DIR": str(home),
                    "OPENCODE_CONFIG_DIR": str(home),
                    "XDG_CONFIG_HOME": str(home),
                    "XDG_DATA_HOME": str(home),
                    "XDG_CACHE_HOME": str(home),
                },
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            ).stdout.strip()
            assert native_executable_sha256(program) == selected.executable_sha256
            identities.append(
                {
                    "agent": selected.agent,
                    "version": version,
                    "executable_sha256": selected.executable_sha256,
                    "public_runtime_roots": list(selected.definition.runtime_roots),
                }
            )
        receipt = {
            "proof_stage": "shared-database overlap explicitly refused; subsequent fresh native Run succeeds",
            "native_identities": identities,
            "runs": [
                {
                    "run_id": detail.run.fleet_run_id,
                    "outcome": detail.outcome.value,
                    "physical_execution_ids": sorted(ids),
                    "lifecycle_events": [event.model_dump(mode="json") for event in events],
                }
                for detail, ids, events in zip(
                    (detail_a, detail_b), process_ids, event_lists, strict=True
                )
            ],
            "refused_while_a_active": {
                "b_run_created": False,
                "b_worker_launched": False,
                "b_native_agent_workload_launched": False,
                "b_model_requests": 0,
                "a_lease_and_worker_unchanged": True,
                "pre_admission_source_and_git_preparation": "allowed; not claimed absent",
            },
            "provider_request_counts": {
                label: len(values) for label, values in case.requests.items()
            },
            "worker_process_samples": samples,
            "delivered_context": {
                label: {
                    "identity": case.prompts[label]["identity"],
                    "producer_implementation_identity": json.loads(
                        case.prompts[label]["repository_evidence"]
                    )["producer_implementation_identity"],
                    "capture_identity": json.loads(case.prompts[label]["repository_evidence"])[
                        "capture_identity"
                    ],
                    "task_skill_and_evidence_retained": True,
                    "other_run_task_and_skill_absent": True,
                }
                for label in ("a", "b")
            },
            "profile_caps": caps,
            "parallel_resource_qualification": "NOT_RUN; overlap refused by current shared-database admission",
        }
        path = tmp_path / "installed-shared-db-receipt.json"
        encoded = json.dumps(receipt, sort_keys=True).encode()
        assert len(encoded) <= 256 * 1024
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
        print(
            json.dumps(
                {
                    "receipt": str(path),
                    "proof_stage": receipt["proof_stage"],
                    "native_identities": identities,
                    "outcomes": [detail_a.outcome.value, detail_b.outcome.value],
                    "profile_caps": caps,
                    "parallel_resource_qualification": receipt["parallel_resource_qualification"],
                },
                sort_keys=True,
            )
        )
    reopened = ApplicationRuntime.build(standard_local=True, startup_purpose="observation")
    try:
        for detail in (detail_a, detail_b):
            retained = reopened.runs.get(detail.run.fleet_run_id)
            assert retained.run == detail.run and retained.operations == detail.operations
            assert retained.lifecycle_events == detail.lifecycle_events
    finally:
        reopened.shutdown(wait_for_runs=True)
