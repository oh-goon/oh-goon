from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

import httpx2
import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentModel,
    AgentProvider,
    AgentProvidersConfig,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
from oh_goon.core.models.execution_identity import ExecutionIdentity
from oh_goon.core.models.fleet_observation import NativeAgentObservation
from oh_goon.core.models.fleet_plan import (
    PLAN_CONTRACT_RETURN_CODE,
    ResolvedCommandStep,
    ResolvedLocalSearchAgentStep,
)
from oh_goon.core.models.fleet_plan_evidence import PlanOutputEvidence
from oh_goon.core.models.fleet_plan_execution_policy import VerificationExecutionPolicy
from oh_goon.core.models.runtime_supervision import RuntimeSupervisionPolicy
from oh_goon.engine.command import CommandResult, CommandSupervisorReceipt, CommandTimeoutExpired
from oh_goon.engine.command_supervision import CommandRuntimeSupervision
from oh_goon.engine.git import GitService
from oh_goon.integrations._fleet_plan_step_evidence import attempt_evidence, step_evidence
from oh_goon.integrations._fleet_plan_step_outcome import from_command, timeout_outcome
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_native_steps import (
    prepare_native_step_input,
    require_native_agent_compatibility,
)
from oh_goon.integrations.fleet_plan_process_isolation import native_profile_command_runner
from oh_goon.integrations.fleet_plan_step_execution import StepRuntime, execute_direct_step
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plan_step_resolution import resolve_delivered_local_search_agent_step

_STUB = r"""#!/usr/bin/python3
import json, os, pathlib, sys, urllib.request, time
mode = sys.argv[sys.argv.index('--fixture-mode') + 1]
prompt = json.loads(sys.stdin.read())
assert prompt['task'] == 'Inspect the captured source'
assert 'Hashmarks' in prompt['repository_evidence']
assert pathlib.Path(prompt['complete_source_manifest']).is_file()
assert pathlib.Path(prompt['captured_skill'], 'SKILL.md').read_text().startswith('# Native fixture')
working = prompt['working_workspace']
assert pathlib.Path(working['root']) == pathlib.Path.cwd()
assert working['root'] != prompt['captured_repository']
assert working['repository_access'] == ('read_only' if mode in {'review', 'wrong-review'} else 'write')
if mode == 'helper-shadowing':
    import shlex, subprocess
    evidence = subprocess.run(shlex.split(prompt['repository_tools']['list']), capture_output=True, text=True, check=True)
    assert json.loads(evidence.stdout)['producer'] == 'Hashmarks'
    assert 'repo/main.py' in json.loads(evidence.stdout)['paths']
configuration = json.loads(pathlib.Path(os.environ['OPENCODE_CONFIG_DIR'], 'opencode.json').read_text())
provider = configuration['providers']['oh-goon']
base = provider['settings']['baseURL']
model = provider['models']['approved']['modelID']
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
request = urllib.request.Request(base + '/chat/completions', data=json.dumps({
    'model': model, 'messages': [{'role': 'user', 'content': 'Native turn'}], 'stream': True,
}).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer oh-goon-local'})
with opener.open(request) as response: assert response.read()
if mode == 'timeout': time.sleep(30)
if mode == 'failure':
    print('native error, not JSON', flush=True)
    sys.exit(7)
if mode == 'malformed':
    print('not native protocol', flush=True)
    sys.exit(0)
if mode == 'review':
    identity = prompt['identity']
    manifest = json.loads(pathlib.Path(prompt['complete_source_manifest']).read_text())
    final = json.dumps(dict(schema='oh-goon.agent-review/v1', **identity, verdict='CLEAN',
        summary='Reviewed captured source', reviewed_paths=[m['path'] for m in manifest['repository']], findings=[]))
elif mode == 'wrong-review':
    final = 'CLEAN'
else:
    pathlib.Path(working['root'], 'native-write.txt').write_text('owned edit')
    final = 'Native work reported'
print(json.dumps({'type': 'step_start'}))
if mode == 'long':
    for index in range(6000):
        print(json.dumps({'type': 'tool_use', 'part': {'id': str(index), 'state': {'status': 'completed'}, 'raw_tool_payload': 'x' * 256}}))
print(json.dumps({'type': 'text', 'part': {'text': final, 'time': {} if mode == 'incomplete' else {'end': 1}}}))
if mode != 'incomplete': print(json.dumps({'type': 'step_finish'}))
if mode == 'bad-utf8':
    sys.stdout.flush()
    time.sleep(0.2)
    os.write(1, b'\xff')
"""


def _case(
    tmp_path: Path, *, mode: str = "report", sandboxed: bool = True
) -> tuple[Path, FrozenAgentStepHandoff, ResolvedLocalSearchAgentStep, StepRuntime]:
    repository = tmp_path / "repos/team/project"
    repository.mkdir(parents=True)
    (repository / "main.py").write_text("def answer():\n    return 42\n")
    if mode == "helper-shadowing":
        shadow = repository / "oh_goon"
        shadow.mkdir()
        (shadow / "__init__.py").write_text(
            "from pathlib import Path\n"
            "Path('untrusted-helper-ran.txt').write_text('repository import executed')\n"
            "raise SystemExit(77)\n"
        )
    skill = tmp_path / "skills/team/inspect"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Native fixture\nInspect complete captured source.\n")
    package = tmp_path / "goons/agents/native"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# agents/native\n")
    (package / "goon.yml").write_text(
        """inputs:
  repo: {type: repository_reference}
  skill: {type: skill_reference}
  acceptance: {type: acceptance_profile}
steps:
- uses: agent
  harness: native
  task: Inspect the captured source
  requested_effects:
    repository_access: """
        + ("read_only" if mode in {"review", "wrong-review"} else "write")
        + """
    recipients: [company]
"""
    )
    plan = tmp_path / "plan.yml"
    plan.write_text("inputs: [repo, skill, acceptance]\ngoons: [agents/native]\n")
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(tmp_path / "goons"))
    _, source = resolver.input_definitions_with_source(plan)
    handoff = resolver.prepare_agent_step_handoff(
        plan,
        inputs={
            "repo": "local:team/project",
            "skill": "local:team/inspect",
            "acceptance": "review",
        },
        repository_root=tmp_path / "repos",
        skill_root=tmp_path / "skills",
        allowed_acceptance=("review",),
        expected_source_sha256=source,
    )
    executable = tmp_path / "native-fixture"
    executable.write_text(_STUB)
    executable.chmod(0o755)
    connection = AgentConnection.select(
        AgentProvidersConfig(
            default_model="approved",
            providers={
                "company": AgentProvider(
                    api="openai-chat-completions",
                    base_url="https://approved.test/v1",
                    api_key_env="PRIVATE_NATIVE_KEY",
                )
            },
            models={"approved": AgentModel(provider="company", model="approved/model")},
        )
    )
    backend = None
    if sandboxed:
        binary = shutil.which("bwrap")
        if binary is None:
            pytest.skip("native Bubblewrap is unavailable")
        backend = BubblewrapProcessIsolation(binary)
    selected = ResolvedNativeAgent(
        agent="opencode",
        profile_name="test",
        definition=NativeAgentDefinition(
            executable=str(executable),
            arguments=("--fixture-mode", mode),
        ),
        profile=NativeAgentProfile(
            backend="bubblewrap" if sandboxed else "local", timeout_seconds=10.0
        ),
        executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        connection=connection,
        backend_configuration_id=backend.configuration_identity() if backend is not None else None,
    )
    role: Literal["review", "report"] = "review" if mode in {"review", "wrong-review"} else "report"
    step = resolve_delivered_local_search_agent_step(handoff, native_agent=selected, role=role)
    packet = handoff.encode_prework_delivery()
    runtime = StepRuntime(
        git=GitService(),
        extensions=None,
        tools=None,
        cancel_requested=None,
        agent_delivery_for=lambda step_id, identity: packet,
        execution_policy=VerificationExecutionPolicy(),
        execution_identity=ExecutionIdentity(digest="sha256:" + "b" * 64),
        native_profile=selected,
        standard_process_isolation=backend,
        workspace_pin=RepositoryWorkspaceAuthority.pin_repository_subject,
    )
    return repository, handoff, step, runtime


@pytest.fixture
def model_transport(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    original = httpx2.AsyncClient

    class Stream(httpx2.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'data: {"done":true}\n\n'

    def provider(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["Authorization"] == "Bearer private-host-value"
        requests.append(json.loads(request.content))
        return httpx2.Response(200, stream=Stream())

    def client(**options: Any) -> httpx2.AsyncClient:
        return original(**options, transport=httpx2.MockTransport(provider))

    monkeypatch.setenv("PRIVATE_NATIVE_KEY", "private-host-value")
    monkeypatch.setattr(httpx2, "AsyncClient", client)
    return requests


@pytest.mark.process
def test_native_step_real_child_receives_frozen_context_and_uses_the_managed_broker(
    tmp_path: Path, model_transport: list[dict[str, Any]]
) -> None:
    repository, handoff, step, runtime = _case(tmp_path)
    outcome = execute_direct_step(repository, step, runtime, step_order=1)
    assert outcome.returncode == 0, outcome.stderr
    assert outcome.stdout == "Native work reported"
    assert (repository / "native-write.txt").read_text() == "owned edit"
    assert outcome.physical_execution_id is not None and outcome.runtime_authority is not None
    assert outcome.standard_isolation is not None
    assert outcome.standard_isolation.broker_socket_mounted

    observation = next(
        item for item in outcome.observations if isinstance(item, NativeAgentObservation)
    )
    assert observation.source_identity == handoff.source.capture_identity
    assert observation.occurrence == step.step_id and observation.settled
    assert observation.authority == "UNTRUSTED_HARNESS_CLAIM"
    assert "Hashmarks initial repository evidence" in outcome.stderr
    assert len(model_transport) == 1
    assert "Hashmarks" in model_transport[0]["messages"][0]["content"]
    assert "private-host-value" not in outcome.stdout + outcome.stderr


@pytest.mark.process
def test_native_helpers_ignore_repository_controlled_python_package(
    tmp_path: Path, model_transport: list[dict[str, Any]]
) -> None:
    repository, _handoff, step, runtime = _case(tmp_path, mode="helper-shadowing")
    outcome = execute_direct_step(repository, step, runtime, step_order=1)
    assert not (repository / "untrusted-helper-ran.txt").exists()
    assert outcome.returncode == 0, outcome.stderr
    assert outcome.stdout == "Native work reported"
    assert (repository / "native-write.txt").read_text() == "owned edit"
    assert len(model_transport) == 1


@pytest.mark.process
@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("failure", 7),
        ("malformed", PLAN_CONTRACT_RETURN_CODE),
        ("incomplete", PLAN_CONTRACT_RETURN_CODE),
    ],
)
def test_native_process_failure_and_unsettled_or_invalid_claims_cannot_report_success(
    tmp_path: Path, model_transport: list[dict[str, Any]], mode: str, expected: int
) -> None:
    repository, _, step, runtime = _case(tmp_path, mode=mode)
    outcome = execute_direct_step(repository, step, runtime, step_order=1)
    assert outcome.returncode == expected, outcome.stderr
    assert outcome.supervisor is not None and outcome.supervisor.final_process_group_quiescent
    assert outcome.runtime_authority is not None
    assert outcome.argv[0] == outcome.runtime_authority.executable_path


@pytest.mark.process
def test_native_relay_decode_failure_refuses_a_preceding_completed_claim(
    tmp_path: Path, model_transport: list[dict[str, Any]]
) -> None:
    repository, _, step, runtime = _case(tmp_path, mode="bad-utf8")
    outcome = execute_direct_step(repository, step, runtime, step_order=1)
    assert len(model_transport) == 1
    assert outcome.returncode != 0
    assert outcome.stdout != "Native work reported"
    assert "UnicodeDecodeError" in outcome.stderr
    assert outcome.physical_execution_id is not None
    assert outcome.supervisor is not None and outcome.supervisor.final_process_group_quiescent
    assert all(
        observation.failed
        for observation in outcome.observations
        if isinstance(observation, NativeAgentObservation)
    )


@pytest.mark.process
def test_long_native_protocol_stream_keeps_bounded_claims_and_original_final_acceptance(
    tmp_path: Path, model_transport: list[dict[str, Any]]
) -> None:
    repository, _, step, runtime = _case(tmp_path, mode="long")
    streamed_bytes = 0

    def observe(step_id: str, attempt: int, stream: str, text: str) -> None:
        nonlocal streamed_bytes
        if stream == "stdout":
            streamed_bytes += len(text.encode())

    outcome = execute_direct_step(repository, step, runtime, step_order=1, output_observer=observe)
    assert outcome.returncode == 0, outcome.stderr
    assert streamed_bytes > 1024 * 1024
    assert outcome.stdout == "Native work reported" and outcome.stdout_evidence is None
    assert outcome.standard_isolation is not None
    observation = next(
        item for item in outcome.observations if isinstance(item, NativeAgentObservation)
    )
    assert observation.native_event_count == 6003
    assert len(observation.events) == 128
    assert observation.omitted_observations == 5874
    assert observation.settled and not observation.failed
    assert "raw_tool_payload" not in observation.model_dump_json()
    assert len(model_transport) == 1


@pytest.mark.process
@pytest.mark.parametrize("mode", ["review", "wrong-review"])
def test_native_read_only_phase_reuses_the_original_typed_acceptor(
    tmp_path: Path, model_transport: list[dict[str, Any]], mode: str
) -> None:
    repository, _, step, runtime = _case(tmp_path, mode=mode, sandboxed=True)
    outcome = execute_direct_step(repository, step, runtime, step_order=1)
    assert outcome.standard_isolation is not None
    assert outcome.standard_isolation.workspace_read_only
    assert outcome.standard_isolation.broker_socket_mounted
    if mode == "review":
        assert outcome.returncode == 0, outcome.stderr
        assert json.loads(outcome.stdout)["verdict"] == "CLEAN"
        assert any(item.kind == "agent_review" for item in outcome.observations)
    else:
        assert outcome.returncode != 0
    assert not (repository / "native-write.txt").exists()


@pytest.mark.parametrize(
    "attack",
    ["no-connection", "profile-drift", "no-policy", "opaque-local", "model-without-config"],
)
def test_native_receiver_refuses_missing_or_conflicting_authority_before_evidence(
    tmp_path: Path, attack: str
) -> None:
    _, handoff, step, runtime = _case(tmp_path)
    selected = step.native_agent
    assert selected is not None
    if attack == "no-connection":
        selected = replace(selected, connection=None)
    elif attack == "profile-drift":
        runtime = replace(runtime, native_profile=None)
    elif attack == "no-policy":
        runtime = replace(runtime, execution_policy=None)
    elif attack == "opaque-local":
        selected = replace(
            selected,
            profile=selected.profile.model_copy(update={"backend": "local"}),
            definition=selected.definition.model_copy(
                update={"configuration_directory": str(tmp_path)}
            ),
        )
    else:
        selected = replace(
            selected,
            definition=selected.definition.model_copy(update={"arguments": ("--model", "foreign")}),
        )
    step = step.model_copy(update={"native_agent": selected})
    if attack != "profile-drift":
        runtime = replace(runtime, native_profile=selected)
    with pytest.raises(FleetPlanInvalid):
        prepare_native_step_input(handoff.encode_prework_delivery(), step, runtime)


@pytest.mark.parametrize(
    "arguments", [("--api-key", "private-key-canary"), ("--api-key=private-key-canary",)]
)
def test_transported_native_selection_rejects_literal_credentials_before_admission(
    arguments: tuple[str, ...],
) -> None:
    catalog = AgentProvidersConfig(
        default_model="approved",
        providers={
            "host": AgentProvider(
                api="openai-chat-completions", base_url="https://example.invalid/v1"
            )
        },
        models={"approved": AgentModel(provider="host", model="approved")},
    )
    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="isolated",
        definition=NativeAgentDefinition(executable="/native/pi", arguments=arguments),
        profile=NativeAgentProfile(backend="bubblewrap", inference_profile="approved"),
        executable_sha256="a" * 64,
        connection=AgentConnection.select(catalog),
    )
    with pytest.raises(FleetPlanInvalid) as error:
        require_native_agent_compatibility(selected)
    assert "private-key-canary" not in str(error.value)


def test_local_native_project_configuration_rejects_before_producer_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    repository, handoff, step, runtime = _case(tmp_path, sandboxed=False)
    (repository / "opencode.json").write_text(
        json.dumps(
            {
                "model": "project/other-model",
                "providers": {"project": {"settings": {"baseURL": "http://127.0.0.1:12345/v1"}}},
            }
        )
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("local native inference launched a producer"),
    )
    with pytest.raises(FleetPlanInvalid, match="isolated.*profile"):
        prepare_native_step_input(handoff.encode_prework_delivery(), step, runtime)


@pytest.mark.process
def test_initial_evidence_uses_private_query_file_and_spends_the_same_step_deadline(
    tmp_path: Path, model_transport: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.integrations import fleet_plan_step_execution as module

    repository, handoff, step, runtime = _case(tmp_path)
    original_factory = native_profile_command_runner
    timeouts: list[float] = []
    query_files: list[Path] = []

    def factory(*args: Any, **kwargs: Any) -> Any:
        bound = original_factory(*args, **kwargs)

        def run(*argv: str, **options: Any) -> Any:
            assert handoff.task not in " ".join(argv)
            timeouts.append(options["timeout"])
            if "--query-file" in argv:
                query = Path(argv[argv.index("--query-file") + 1])
                assert query.read_text() == handoff.task
                assert query.stat().st_mode & 0o777 == 0o600
                query_files.append(query)
                time.sleep(0.25)
            return bound(*argv, **options)

        return run

    monkeypatch.setattr(module, "native_profile_command_runner", factory)
    outcome = execute_direct_step(repository, step, runtime, step_order=1)
    assert outcome.returncode == 0, outcome.stderr
    assert len(timeouts) == 2 and timeouts[1] < timeouts[0] - 0.2
    assert query_files and all(not path.exists() for path in query_files)


def test_native_receiver_rejects_fresh_per_process_watchdog_before_producer_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    repository, _, step, runtime = _case(tmp_path)
    supervision = CommandRuntimeSupervision(
        policy=RuntimeSupervisionPolicy(
            max_runtime_seconds=10,
            heartbeat_interval_seconds=1,
            heartbeat_timeout_seconds=3,
            termination_grace_seconds=1,
            quiescence_timeout_seconds=1,
            yield_deadline_seconds=1,
            policy_revision="per-process-only",
        ),
        generation=1,
        heartbeat=lambda identity, generation: None,
        lease_valid=lambda identity, generation: True,
        semantic_progress=lambda identity, generation: None,
    )
    runtime = replace(runtime, runtime_supervision=supervision)
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("unsupported deadline launched a producer"),
    )

    with pytest.raises(FleetPlanInvalid, match="whole-Step deadline"):
        execute_direct_step(repository, step, runtime, step_order=1)


@pytest.mark.parametrize("timed_out", [False, True])
def test_terminal_full_stream_evidence_survives_step_and_attempt_projection(
    timed_out: bool,
) -> None:
    raw = b"earlier repaint\r" * 10000 + b"last frame"
    summary = PlanOutputEvidence(
        sha256=hashlib.sha256(raw).hexdigest(),
        byte_count=len(raw),
        line_count=len(raw.decode().splitlines()),
    )
    tail = "[Earlier terminal output omitted; retained tail follows]\nlast frame"
    step = ResolvedCommandStep(step_id="terminal", argv=("native",))
    if timed_out:
        error = CommandTimeoutExpired(
            ("native",), 1, output=tail, supervisor=CommandSupervisorReceipt()
        )
        error.stdout_evidence = summary
        outcome = timeout_outcome(step, error)
    else:
        outcome = from_command(CommandResult(0, tail, "", stdout_evidence=summary))
    outcome = replace(outcome, stderr="Initial Hashmarks evidence\n" + outcome.stderr)
    attempt = attempt_evidence(
        step=step,
        attempt=1,
        outcome=outcome,
        repository_state_before="before",
        repository_state_after="after",
        worktree_files_after=(),
    )
    execution = step_evidence(
        step=step,
        outcome=outcome,
        repository_state_before="before",
        repository_state_after="after",
        worktree_files_after=(),
        attempts=(attempt,),
        artifacts=(),
    )
    assert execution.stdout_evidence == attempt.stdout_evidence == summary
    assert summary.sha256 != hashlib.sha256(tail.encode()).hexdigest()
    assert execution.stderr_evidence == attempt.stderr_evidence
    assert execution.stderr_evidence.byte_count == len(outcome.stderr.encode()) > 0


@pytest.mark.process
def test_native_timeout_retains_physical_failure_evidence_without_accepting_completion(
    tmp_path: Path, model_transport: list[dict[str, Any]]
) -> None:
    repository, _, step, runtime = _case(tmp_path, mode="timeout", sandboxed=True)
    assert step.native_agent is not None
    selected = replace(
        step.native_agent,
        profile=step.native_agent.profile.model_copy(update={"timeout_seconds": 5.0}),
    )
    step = step.model_copy(update={"native_agent": selected})
    runtime = replace(runtime, native_profile=selected)
    outcome = execute_direct_step(repository, step, runtime, step_order=1)
    assert len(model_transport) == 1  # The native workload reached approved inference.
    assert outcome.returncode == 124
    assert outcome.duration_ms > 0 and outcome.physical_execution_id is not None
    assert outcome.supervisor is not None and outcome.supervisor.cleanup_reason == "timeout"
    assert outcome.supervisor.final_process_group_quiescent
    assert outcome.supervisor.termination_receipt is not None
    assert outcome.supervisor.termination_receipt.quiescent
    assert outcome.runtime_authority is not None
    assert "timed out" in outcome.stderr
    assert not any(isinstance(item, NativeAgentObservation) for item in outcome.observations)
    # Killing the native monitor can prevent its final successful-exec status.
    # The Plan still retains selected profile intent; no receipt is synthesized.
    assert outcome.standard_isolation is None
