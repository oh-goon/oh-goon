"""Owned native service turns retain actual command-owner shutdown evidence."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
import tomllib
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any, cast
from uuid import UUID

import pytest

from oh_goon.core.models.agent_providers import (
    AgentProviderError,
    CodexServiceConnection,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.agent_review import AgentAdjudicationRecord
from oh_goon.core.models.fleet_plan import ResolvedAgentStep, ResolvedNativeAgentWorkload
from oh_goon.core.models.fleet_plan_values import HostEnvironmentPart, ResolvedEnvironmentBinding
from oh_goon.core.path_layouts import AttemptPathLayout
from oh_goon.core.ports.diagnostics import DiagnosticEmitter
from oh_goon.core.ports.step_output import StepOutputObserver
from oh_goon.engine.command import (
    CommandCancelledError,
    CommandResult,
    CommandSupervisorReceipt,
    run_command,
)
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.engine.git import GitService
from oh_goon.integrations import agent_codex_service_execution as execution
from oh_goon.integrations.agent_codex_service import NativeCodexCleanupIncomplete
from oh_goon.integrations.fleet_plan_step_execution import (
    StepRuntime,
    _StepProcess,
    _with_output_observer,
)
from oh_goon.runtime.diagnostics import DiagnosticRouter, JsonlDiagnosticSink


def execute_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fault: str | None = None,
    output_observer: StepOutputObserver | None = None,
    diagnostics: DiagnosticEmitter | None = None,
    notifications: list[dict[str, Any]] | None = None,
    protect_host_secret: bool = False,
    output_schema: dict[str, Any] | None = None,
) -> tuple[
    Callable[[], execution.CodexServiceExecution],
    list[object],
    list[CommandResult | CommandCancelledError],
]:
    repository, control = tmp_path / "repository", tmp_path / "control"
    repository.mkdir()
    control.mkdir()
    cancelled = Event()
    release = tmp_path / "native-exit"
    events: list[object] = []
    physical: list[CommandResult | CommandCancelledError] = []
    native_environment = {
        "OPENAI_API_KEY": "SYNTHETIC_NATIVE_PROVIDER_SECRET",
        "HOME": str(tmp_path / "native-home"),
        "CODEX_HOME": str(tmp_path / "native-home" / "native-state"),
        "XDG_CONFIG_HOME": str(tmp_path / "native-configuration"),
    }
    inherited_control = {
        name: "SYNTHETIC_PARENT_CONTROL_SECRET"
        for name in (
            "CODEX_INTERNAL_ORIGINATOR_OVERRIDE",
            "CODEX_VERSION",
            "CODEX_THREAD_ID",
            "CODEX_SESSION_ID",
            "CODEX_PERMISSION_PROFILE",
            "CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN",
            "NODE_REPL_AUTH_TOKEN",
        )
    }
    public_program = tmp_path / "public-runtime" / "codex"
    public_program.parent.mkdir()
    public_program.write_text("#!/bin/sh\nexit 99\n")
    public_program.chmod(0o700)
    for name in ("HOME", "CODEX_HOME", "XDG_CONFIG_HOME"):
        Path(native_environment[name]).mkdir(parents=True, exist_ok=True)
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="owned-service",
        definition=NativeAgentDefinition(executable=str(public_program)),
        profile=NativeAgentProfile(
            codex_service=CodexServiceConnection(
                home=str(tmp_path / "native-home" / "native-state"),
                model="native-model",
                reasoning_effort="high",
            )
        ),
        executable_sha256="0" * 64,
    )

    def command_owner(*argv: str, **kwargs: Any) -> CommandResult:
        if kwargs.get("stream_output") is not None:
            kwargs["stream_output"]("stderr", "RAW_SYNTHETIC_PRIVATE_PROTOCOL\n")
        environment = kwargs["env"]
        assert all(environment.get(name) == value for name, value in native_environment.items())
        assert not inherited_control.keys() & environment.keys()
        if argv[-1] == "--version":
            events.append("version_probe")
            version = {
                "version": "codex-cli 0.999.0",
                "previous_version": "codex-cli 0.163.0",
            }.get(fault or "", "codex-cli 0.164.0")
            result = run_command(
                sys.executable,
                "-I",
                "-c",
                "import sys; print(sys.argv[1])",
                version,
                cwd=repository,
                env={"PATH": "/usr/bin:/bin"},
                timeout=kwargs["timeout"],
                cancel_requested=kwargs["cancel_requested"],
                require_process_group_quiescence=True,
                input_data=kwargs.get("input_data"),
            )
            if fault is not None and fault.startswith("version-stop-"):
                reason = fault.removeprefix("version-stop-")
                result = replace(
                    result,
                    supervisor=CommandSupervisorReceipt(
                        cleanup_reason=cast(Any, reason),
                        process_group_cleanup_requested=True,
                    ),
                )
            events.append(("version_receipt", result))
            return result
        events.append("process_birth")
        if fault == "dead_before_socket":
            script = "raise SystemExit(7)"
        else:
            script = (
                "import pathlib,sys,time\n"
                "pathlib.Path(sys.argv[1]).touch()\n"
                "while not pathlib.Path(sys.argv[2]).exists(): time.sleep(.01)\n"
            )
        try:
            result = run_command(
                sys.executable,
                "-I",
                "-c",
                script,
                argv[-1].removeprefix("unix://"),
                str(release),
                cwd=repository,
                env={"PATH": "/usr/bin:/bin"},
                timeout=kwargs["timeout"],
                cancel_requested=kwargs["cancel_requested"],
                require_process_group_quiescence=True,
                input_data=kwargs["input_data"],
            )
        except CommandCancelledError as error:
            physical.append(error)
            events.append("process_closed")
            raise
        physical.append(result)
        events.append("process_closed")
        return result

    class NativeProtocol:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.metadata = {"service_version": None}
            self._message = 0

        async def __aenter__(self) -> NativeProtocol:
            events.append("connect")
            return self

        async def __aexit__(self, *_args: object) -> None:
            events.append("disconnect")
            if fault == "normal_daemon_exit":
                release.touch()
                deadline = time.monotonic() + 2
                while not physical and time.monotonic() < deadline:
                    await asyncio.sleep(0.01)
                assert physical, "real command owner did not observe the normal daemon exit"
            if fault == "transport_cleanup":
                raise NativeCodexCleanupIncomplete("Native transport cleanup is incomplete")

        def require_native_home_separation(
            self, _roots: tuple[Path, ...], *, expected_home: Path | None = None
        ) -> None:
            assert expected_home == Path(native_environment["CODEX_HOME"])
            events.append("store_separation")
            if fault == "store_overlap":
                raise AgentProviderError("Native Codex state overlaps an agent-readable root")

        async def start_thread(self, **_kwargs: Any) -> dict[str, Any]:
            events.append("thread_start")
            assert _kwargs["configuration"] == {
                "features.incremental_tools": False,
                "mcp_servers": {"PRIVATE_NATIVE_REGISTRATION": {"enabled": False}},
            }
            if fault == "mcp_invalid_configuration":
                raise AgentProviderError("Native Codex service refused the request")
            return {"thread": {"id": "owned-thread"}}

        async def mcp_exclusion_configuration(self) -> dict[str, Any]:
            events.append("mcp_discovery")
            if fault == "mcp_inventory":
                raise AgentProviderError(
                    "Native Codex MCP inventory is incomplete or exceeds its bound"
                )
            return {"mcp_servers": {"PRIVATE_NATIVE_REGISTRATION": {"enabled": False}}}

        async def require_mcp_servers_excluded(self, thread: str) -> None:
            assert thread == "owned-thread"
            events.append("mcp_gate")
            if fault == "mcp":
                try:
                    raise ValueError("RAW_SYNTHETIC_PRIVATE_ERROR_CHAIN")
                except ValueError as error:
                    raise AgentProviderError(
                        "Native Codex MCP tool exclusion was not established"
                    ) from error

        async def feature_flags(self, thread: str) -> dict[str, bool]:
            assert thread == "owned-thread"
            events.append("feature_gate")
            return {name: fault == "features" for name in execution._DISABLED_FEATURES}

        async def command_exec(self, *_args: object, **_kwargs: Any) -> dict[str, Any]:
            events.append("sandbox_qualification")
            if fault == "qualification_missing":
                return {}
            return {
                "exitCode": {
                    "qualification": 1,
                    "qualification_boolean": False,
                    "qualification_float": 0.0,
                    "qualification_null": None,
                    "qualification_array": [],
                }.get(fault or "", 0)
            }

        async def start_turn(self, thread: str, task: str, **_kwargs: Any) -> dict[str, Any]:
            assert thread == "owned-thread" and task == "Captured task"
            assert _kwargs["output_schema"] == output_schema
            events.append("model_turn")
            if fault == "turn_missing":
                return {}
            return {
                "turn": {
                    "turn_null": None,
                    "turn_array": [],
                    "turn_identifier_missing": {},
                    "turn_identifier_null": {"id": None},
                    "turn_identifier_boolean": {"id": False},
                    "turn_identifier_blank": {"id": " "},
                    "turn_identifier_oversized": {"id": "t" * 257},
                    "turn_identifier_control": {"id": "PRIVATE\nTURN"},
                }.get(fault or "", {"id": "owned-turn"})
            }

        async def next_notification(self, thread: str) -> dict[str, Any]:
            assert thread == "owned-thread"
            if notifications is not None and notifications:
                return notifications.pop(0)
            if fault == "cancel":
                cancelled.set()
                await asyncio.sleep(0.01)
                return {"method": "item/agentMessage/delta", "params": {"threadId": thread}}
            if notifications is not None:
                raise AssertionError("native fixture notification script was exhausted")
            self._message += 1
            if self._message == 1:
                return {
                    "method": "item/completed",
                    "params": {
                        "threadId": thread,
                        "turnId": "owned-turn",
                        "item": {"type": "agentMessage", "text": "Native final report"},
                    },
                }
            return {
                "method": "turn/completed",
                "params": {
                    "threadId": thread,
                    "turn": {
                        "id": "foreign-turn" if fault == "foreign_terminal" else "owned-turn",
                        "status": "completed",
                        "error": None,
                    },
                },
            }

    monkeypatch.setattr(execution, "CodexServiceClient", NativeProtocol)
    process = _StepProcess(
        paths=AttemptPathLayout.from_root(tmp_path / "attempt"),
        environment={},
        host_environment={**native_environment, **inherited_control},
        command_runner=command_owner,
        git_command_runner=command_owner,
        managed_runtime=None,
    )
    process = _with_output_observer(
        process,
        ResolvedAgentStep(
            timeout_seconds=3600,
            workload=ResolvedNativeAgentWorkload(native_agent=selected),
            step_id="native-service",
            delivery_sha256="0" * 64,
            handoff_identity="original-frozen-handoff",
            source_step_id="native-service",
            environment=(
                ResolvedEnvironmentBinding(
                    name="ADMITTED_SECRET",
                    parts=(HostEnvironmentPart(name="NODE_REPL_AUTH_TOKEN"),),
                ),
            )
            if protect_host_secret
            else (),
        ),
        3,
        output_observer,
        diagnostics,
        "original-fleet-run",
        "original-repository",
    )
    runtime = StepRuntime(
        git=GitService(), extensions=None, tools=None, cancel_requested=cancelled.is_set
    )

    def invoke() -> execution.CodexServiceExecution:
        return execution.execute_codex_service_run(
            selected,
            repository=repository,
            instructions=b"Frozen Skill and Hashmarks instructions",
            task="Captured task",
            read_roots=(tmp_path / "public-runtime",),
            control=control,
            process=process,
            runtime=runtime,
            deadline=time.monotonic() + 5,
            read_only=True,
            output_schema=output_schema,
        )

    return invoke, events, physical


def test_service_turn_success_retains_actual_owned_daemon_cancellation_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke, events, physical = execute_fixture(tmp_path, monkeypatch)
    result = invoke()
    assert len(physical) == 1 and isinstance(physical[0], CommandCancelledError)
    shutdown = physical[0]
    assert result.outcome.supervisor is shutdown.supervisor
    assert result.outcome.runtime_authority is shutdown.runtime_authority
    assert result.outcome.physical_execution_id == shutdown.physical_execution_id
    assert shutdown.supervisor.process_group_cleanup_requested is True
    assert shutdown.supervisor.cleanup_reason == "cancel"
    assert shutdown.supervisor.final_process_group_quiescent is True
    assert shutdown.supervisor.termination_receipt is not None
    assert result.outcome.stdout == "Native final report"
    assert result.outcome.returncode == 0 and result.protocol.settled is True
    assert result.outcome.completion == "service_shutdown"
    assert result.outcome.observed_returncode is None
    assert events.index("mcp_gate") < events.index("model_turn")
    assert events.index("store_separation") < events.index("mcp_discovery")
    assert events.index("mcp_discovery") < events.index("thread_start") < events.index("mcp_gate")
    assert events.count("thread_start") == 1 and events.count("model_turn") == 1
    assert "PRIVATE_NATIVE_REGISTRATION" not in result.execution.model_dump_json()
    assert events.index("sandbox_qualification") < events.index("model_turn")
    assert events.index("disconnect") < events.index("process_closed")


@pytest.mark.skipif(sys.platform != "linux", reason="Linux AF_UNIX pathname transport")
def test_long_service_socket_refuses_before_private_state_or_native_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ("é" * 40)
    root.mkdir()
    invoke, events, physical = execute_fixture(root, monkeypatch)
    with pytest.raises(AgentProviderError, match="107-byte"):
        invoke()
    assert events == []
    assert physical == []
    assert not (root / "control" / "service-private").exists()
    assert not (root / "control" / "service-scratch").exists()


def test_service_turn_transports_the_selected_adjudication_schema_without_rewriting_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(execution, "uuid4", lambda: UUID(int=1))
    schema = AgentAdjudicationRecord.model_json_schema(by_alias=True)
    schema["required"] = list(schema["properties"])
    schema["properties"]["candidate_identity"]["enum"] = ["agent-source-closure-v1-" + "a" * 64]
    schema["properties"]["scope_identity"]["enum"] = ["agent-review-scope-v1-" + "a" * 64]
    invoke, events, physical = execute_fixture(tmp_path, monkeypatch, output_schema=schema)
    result = invoke()
    assert events.count("model_turn") == 1
    assert result.protocol.final_text == "Native final report"
    assert physical[0].supervisor.final_process_group_quiescent
    # Reuse exactly the same configuration only after its owner proves quiescence.
    shutil.rmtree(tmp_path / "control" / "service-private")
    shutil.rmtree(tmp_path / "control" / "service-scratch")
    schema["properties"]["candidate_identity"]["enum"] = ["agent-source-closure-v1-" + "b" * 64]
    changed = invoke()
    assert events.count("model_turn") == 2
    assert physical[1].supervisor.final_process_group_quiescent
    assert changed.execution.permission_profile_id == result.execution.permission_profile_id
    assert changed.execution.configuration_id != result.execution.configuration_id


@pytest.mark.parametrize("observer_fails", [False, True])
def test_safe_service_failure_reaches_original_stream_only_after_process_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, observer_fails: bool
) -> None:
    received: list[tuple[str, int, str, str]] = []

    def observe(step: str, attempt: int, stream: str, text: str) -> None:
        assert len(physical) == 1
        assert physical[0].supervisor.final_process_group_quiescent is True
        received.append((step, attempt, stream, text))
        if observer_fails:
            raise RuntimeError("synthetic live-output observer failure")

    invoke, events, physical = execute_fixture(
        tmp_path, monkeypatch, fault="mcp", output_observer=observe
    )
    with pytest.raises(
        execution.CodexServiceExecutionFailure, match="MCP tool exclusion"
    ) as failure:
        invoke()
    assert "model_turn" not in events
    assert received == [
        (
            "native-service",
            3,
            "stderr",
            "Native Codex MCP tool exclusion was not established\n",
        )
    ]
    assert failure.value.outcome.supervisor is physical[0].supervisor
    assert failure.value.outcome.physical_execution_id == physical[0].physical_execution_id
    assert failure.value.outcome.returncode != 0


def test_safe_service_failure_survives_runtime_shutdown_without_live_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "passive-diagnostics" / "diagnostics.jsonl"
    router = DiagnosticRouter((JsonlDiagnosticSink(path),))
    invoke, events, physical = execute_fixture(
        tmp_path, monkeypatch, fault="mcp", diagnostics=router
    )
    with pytest.raises(execution.CodexServiceExecutionFailure) as failure:
        invoke()
    assert "model_turn" not in events
    assert failure.value.outcome.supervisor is physical[0].supervisor
    assert physical[0].supervisor.final_process_group_quiescent is True
    del router, invoke
    # Independent readback needs no live store, service or native private files.
    retained = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(retained) == 1
    event = retained[0]
    assert event["fleet_run_id"] == "original-fleet-run"
    assert event["repository_id"] == "original-repository"
    assert event["step_id"] == "native-service"
    assert event["operation"] == "native_failure_attempt_3"
    assert event["exception_type"] == "AgentProviderError"
    assert "MCP tool exclusion" in event["message"]
    assert event["message"] in event["traceback"]
    journal_context = "\n".join(item.summary for item in failure.value.safe_diagnostics)
    assert event["message"] in journal_context
    assert event["traceback"] in journal_context
    assert "not a model claim or authority" in journal_context
    assert "RAW_SYNTHETIC_PRIVATE" not in journal_context
    assert "RAW_SYNTHETIC_PRIVATE" not in json.dumps(retained)
    assert "SYNTHETIC_NATIVE_PROVIDER_SECRET" not in json.dumps(retained)
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("fault", ("mcp_inventory", "mcp_invalid_configuration"))
def test_native_mcp_discovery_or_disable_rejection_closes_original_process_before_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    invoke, events, physical = execute_fixture(tmp_path, monkeypatch, fault=fault)
    with pytest.raises(execution.CodexServiceExecutionFailure) as error:
        invoke()
    assert events.count("mcp_discovery") == 1
    assert events.count("thread_start") == (fault == "mcp_invalid_configuration")
    assert "mcp_gate" not in events and "model_turn" not in events
    assert len(physical) == 1 and physical[0].supervisor.final_process_group_quiescent
    assert error.value.outcome.supervisor is physical[0].supervisor
    assert "PRIVATE_NATIVE_REGISTRATION" not in str(error.value)


@pytest.mark.parametrize("fault,version", [("version", "0.999.0"), ("previous_version", "0.163.0")])
def test_unqualified_native_cli_version_blocks_before_daemon_and_model_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str, version: str
) -> None:
    invoke, events, physical = execute_fixture(tmp_path, monkeypatch, fault=fault)
    with pytest.raises(AgentProviderError, match="version"):
        invoke()
    assert "version_probe" in events
    assert "process_birth" not in events and "connect" not in events and "model_turn" not in events
    assert not physical
    receipts = [
        item[1] for item in events if isinstance(item, tuple) and item[0] == "version_receipt"
    ]
    assert len(receipts) == 1 and isinstance(receipts[0], CommandResult)
    assert receipts[0].stdout.strip() == f"codex-cli {version}"
    assert receipts[0].supervisor.final_process_group_quiescent is True


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("cancel", "CANCELLED"),
        ("timeout", "TIMED_OUT"),
        ("authority", "AUTHORITY_LOST"),
        ("output_limit", "OUTPUT_LIMITED"),
    ],
)
def test_interrupted_version_probe_never_starts_the_daemon_or_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str, expected: str
) -> None:
    from oh_goon.core.models.fleet_plan import ResolvedCommandStep
    from oh_goon.integrations._fleet_plan_step_outcome import settle_step_execution

    invoke, events, physical = execute_fixture(
        tmp_path, monkeypatch, fault="version-stop-" + reason
    )
    with pytest.raises(execution.CodexServiceExecutionFailure) as error:
        invoke()
    assert "version_probe" in events and "process_birth" not in events
    assert "connect" not in events and "model_turn" not in events and not physical
    settlement = settle_step_execution(
        ResolvedCommandStep(step_id="probe", argv=("codex", "--version")),
        error.value.outcome,
    )
    assert settlement.outcome == expected
    assert settlement.cleanup == "VERIFIED" and settlement.observed_returncode == 0


@pytest.mark.parametrize("fault", ["store_overlap", "mcp", "features", "qualification"])
def test_unproven_service_capability_blocks_model_and_closes_owned_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    invoke, events, physical = execute_fixture(tmp_path, monkeypatch, fault=fault)
    with pytest.raises(execution.CodexServiceExecutionFailure) as failure:
        invoke()
    assert "model_turn" not in events
    assert len(physical) == 1 and isinstance(physical[0], CommandCancelledError)
    assert failure.value.outcome.supervisor is physical[0].supervisor
    assert failure.value.outcome.physical_execution_id == physical[0].physical_execution_id
    assert failure.value.outcome.returncode != 0
    assert physical[0].supervisor.final_process_group_quiescent is True
    assert events[-1] == "process_closed"


@pytest.mark.parametrize("fault", ["foreign_terminal", "transport_cleanup", "normal_daemon_exit"])
def test_missing_terminal_or_cleanup_proof_cannot_be_reported_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    invoke, events, physical = execute_fixture(tmp_path, monkeypatch, fault=fault)
    with pytest.raises(AgentProviderError):
        invoke()
    assert "model_turn" in events
    assert len(physical) == 1
    assert physical[0].supervisor.final_process_group_quiescent is True
    assert isinstance(
        physical[0], CommandResult if fault == "normal_daemon_exit" else CommandCancelledError
    )


@pytest.mark.parametrize(
    "fault",
    [
        "qualification_missing",
        "qualification_boolean",
        "qualification_float",
        "qualification_null",
        "qualification_array",
    ],
)
def test_malformed_sandbox_qualification_never_starts_model_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    invoke, events, physical = execute_fixture(tmp_path, monkeypatch, fault=fault)
    with pytest.raises(execution.CodexServiceExecutionFailure, match="qualification") as failure:
        invoke()
    assert "sandbox_qualification" in events and "model_turn" not in events
    assert len(physical) == 1 and isinstance(physical[0], CommandCancelledError)
    assert failure.value.outcome.returncode != 0
    assert failure.value.outcome.supervisor is physical[0].supervisor
    assert failure.value.outcome.physical_execution_id == physical[0].physical_execution_id
    assert physical[0].supervisor.final_process_group_quiescent is True


@pytest.mark.parametrize(
    "fault",
    [
        "turn_missing",
        "turn_null",
        "turn_array",
        "turn_identifier_missing",
        "turn_identifier_null",
        "turn_identifier_boolean",
        "turn_identifier_blank",
        "turn_identifier_oversized",
        "turn_identifier_control",
    ],
)
def test_malformed_turn_acceptance_is_a_safe_failure_with_actual_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    invoke, events, physical = execute_fixture(tmp_path, monkeypatch, fault=fault)
    with pytest.raises(execution.CodexServiceExecutionFailure, match="owned turn") as failure:
        invoke()
    assert "model_turn" in events
    assert len(physical) == 1 and isinstance(physical[0], CommandCancelledError)
    assert failure.value.outcome.returncode != 0
    assert "PRIVATE" not in failure.value.outcome.stderr
    assert failure.value.outcome.supervisor is physical[0].supervisor
    assert failure.value.outcome.physical_execution_id == physical[0].physical_execution_id
    assert physical[0].supervisor.final_process_group_quiescent is True


def test_dead_service_before_readiness_never_submits_model_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke, events, physical = execute_fixture(tmp_path, monkeypatch, fault="dead_before_socket")
    with pytest.raises(AgentProviderError, match="before readiness"):
        invoke()
    assert "connect" not in events and "model_turn" not in events
    assert len(physical) == 1 and isinstance(physical[0], CommandResult)
    assert physical[0].returncode == 7
    assert physical[0].supervisor.final_process_group_quiescent is True


def test_run_cancellation_keeps_actual_daemon_cleanup_and_never_returns_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke, events, physical = execute_fixture(tmp_path, monkeypatch, fault="cancel")
    with pytest.raises(OperationCancelledError) as cancelled:
        invoke()
    assert cancelled.value.__cause__ is physical[0]
    assert "model_turn" in events and "disconnect" in events
    assert len(physical) == 1 and isinstance(physical[0], CommandCancelledError)
    assert physical[0].supervisor.cleanup_reason == "cancel"
    assert physical[0].supervisor.final_process_group_quiescent is True


def test_service_policy_keeps_review_git_and_control_roots_protected(tmp_path: Path) -> None:
    repository, control = tmp_path / "repository", tmp_path / "control"
    socket = control / "private" / "agent.sock"
    scratch, public_runtime = control / "scratch", tmp_path / "public-runtime"
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="owned-service",
        definition=NativeAgentDefinition(executable="/public/native/codex"),
        profile=NativeAgentProfile(
            codex_service=CodexServiceConnection(
                home=str(tmp_path / "native-home" / "native-state"),
                model="native-model",
                reasoning_effort="high",
            )
        ),
        executable_sha256="0" * 64,
    )
    argv = execution.service_arguments(
        selected,
        repository=repository,
        read_roots=(public_runtime,),
        control=control,
        scratch=scratch,
        socket=socket,
        profile="frozen_profile",
        read_only=True,
    )
    settings = [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "-c"]
    native = tomllib.loads("\n".join(settings))
    policy = native["permissions"]["frozen_profile"]
    assert policy["filesystem"] == {
        ":root": "deny",
        ":minimal": "read",
        str(public_runtime): "read",
        str(repository): "read",
        str(repository / ".git"): "read",
        str(repository / ".codex"): "read",
        str(repository / ".codex" / "**"): "deny",
        str(control): "read",
        str(socket.parent): "deny",
        str(scratch): "write",
    }
    assert policy["network"] == {"enabled": False}
    assert native["approval_policy"] == "never"
    assert native["allow_login_shell"] is False
    assert native["shell_environment_policy"]["inherit"] == "none"
    assert native["shell_environment_policy"]["include_only"] == ["PATH", "TMPDIR"]
    assert argv[-3:] == ("app-server", "--listen", "unix://" + str(socket))


def _visible_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": "item/completed",
        "params": {"threadId": "owned-thread", "turnId": "owned-turn", "item": item},
    }


def _visible_terminal(*, failed: bool = False) -> dict[str, Any]:
    return {
        "method": "turn/completed",
        "params": {
            "threadId": "owned-thread",
            "turn": {
                "id": "owned-turn",
                "status": "failed" if failed else "completed",
                "error": {"message": "RAW_PRIVATE_RPC_ERROR"} if failed else None,
            },
        },
    }


def test_completed_visible_items_retain_actions_results_and_exclude_reasoning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = [
        _visible_item({"type": "agentMessage", "text": "I will inspect the targeted failure."}),
        _visible_item({"type": "reasoning", "text": "PRIVATE_REASONING_CANARY"}),
        _visible_item(
            {
                "type": "commandExecution",
                "command": "pytest tests/test_target.py",
                "exitCode": 0,
                "aggregatedOutput": "1 passed in 0.10s",
                "cwd": "PRIVATE_CWD_CANARY",
                "commandActions": [{"private": "PRIVATE_ACTION_METADATA"}],
            }
        ),
        _visible_item({"type": "agentMessage", "text": "The targeted command completed."}),
        _visible_terminal(),
    ]
    invoke, _, _ = execute_fixture(tmp_path, monkeypatch, notifications=script)
    result = invoke()
    retained = "\n".join(item.summary for item in result.protocol.observations)
    assert "inspect the targeted failure" in retained
    assert "pytest tests/test_target.py" in retained
    assert "exitCode: 0" in retained
    assert "1 passed in 0.10s" in retained
    assert "PRIVATE_REASONING_CANARY" not in retained
    assert "PRIVATE_CWD_CANARY" not in retained
    assert "PRIVATE_ACTION_METADATA" not in retained
    assert len(retained) <= 28000


def test_tool_stacktrace_survives_model_error_with_original_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = [
        _visible_item(
            {
                "type": "commandExecution",
                "command": "pytest tests/test_target.py",
                "exitCode": 1,
                "aggregatedOutput": "Traceback (most recent call last):\n  File target.py, line 7\nAssertionError: expected 2, got 1",
            }
        ),
        _visible_terminal(failed=True),
    ]
    invoke, _, physical = execute_fixture(tmp_path, monkeypatch, notifications=script)
    with pytest.raises(execution.CodexServiceExecutionFailure) as failure:
        invoke()
    retained = "\n".join(item.summary for item in failure.value.safe_diagnostics)
    assert "pytest tests/test_target.py" in retained
    assert "Traceback (most recent call last)" in retained
    assert "AssertionError: expected 2, got 1" in retained
    assert "exitCode: 1" in retained
    assert "RAW_PRIVATE_RPC_ERROR" not in retained
    assert failure.value.native_event_count == 2
    assert failure.value.outcome.supervisor is physical[0].supervisor
    assert physical[0].supervisor.final_process_group_quiescent is True


def test_native_visible_text_redacts_complete_secrets_before_clipping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protected = "SYNTHETIC_PARENT_CONTROL_SECRET"
    raw = "x" * 1840 + protected + "y" * 4100 + "\npassword=synthetic-visible-secret"
    script = [
        _visible_item(
            {
                "type": "commandExecution",
                "command": "cat public-output.txt",
                "exitCode": 0,
                "aggregatedOutput": raw,
            }
        ),
        _visible_terminal(),
    ]
    invoke, _, _ = execute_fixture(
        tmp_path, monkeypatch, notifications=script, protect_host_secret=True
    )
    result = invoke()
    retained = "\n".join(item.summary for item in result.protocol.observations)
    assert protected not in retained
    assert "SYNTHETIC_PARENT" not in retained
    assert "synthetic-visible-secret" not in retained
    assert "visible characters omitted" in retained
    assert "***" in retained
    assert "password=[REDACTED]" in retained
    assert all(len(item.summary) <= 4000 for item in result.protocol.observations)


def test_missing_optional_command_output_is_explicit_and_shared_budget_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = [
        _visible_item({"type": "commandExecution", "command": "pytest"}),
        *(
            _visible_item({"type": "agentMessage", "text": "visible message " + "x" * 5000})
            for _ in range(12)
        ),
        _visible_terminal(),
    ]
    invoke, _, _ = execute_fixture(tmp_path, monkeypatch, notifications=script)
    result = invoke()
    retained = "\n".join(item.summary for item in result.protocol.observations)
    assert "exitCode: unavailable" in retained
    assert "aggregatedOutput:\nunavailable" in retained
    assert sum(len(item.summary) for item in result.protocol.observations) <= 28000
    assert len(result.protocol.observations) <= 127
    assert result.protocol.omitted_observations > 0


def test_cancelled_service_preserves_visible_output_without_fabricating_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "passive-diagnostics" / "diagnostics.jsonl"
    script = [
        _visible_item(
            {
                "type": "commandExecution",
                "command": "pytest tests/test_target.py",
                "exitCode": 1,
                "aggregatedOutput": "Traceback: target.py:7\nAssertionError: visible-before-cancel password=cancel-secret",
            }
        )
    ]
    invoke, _, physical = execute_fixture(
        tmp_path,
        monkeypatch,
        fault="cancel",
        notifications=script,
        diagnostics=DiagnosticRouter((JsonlDiagnosticSink(path),)),
    )
    with pytest.raises(execution.CodexServiceExecutionCancelled) as cancelled:
        invoke()
    assert cancelled.value.__cause__ is physical[0]
    assert cancelled.value.outcome.supervisor is physical[0].supervisor
    assert physical[0].supervisor.final_process_group_quiescent is True
    del invoke
    retained = [json.loads(line) for line in path.read_text().splitlines()]
    visible = [item for item in retained if item["source"] == "native_visible_claim"]
    assert len(visible) == 1
    assert visible[0]["fleet_run_id"] == "original-fleet-run"
    assert visible[0]["step_id"] == "native-service"
    assert visible[0]["operation"] == "visible_native_claim_attempt_3"
    assert visible[0]["execution_affected"] is False
    assert "AssertionError: visible-before-cancel" in visible[0]["message"]
    assert "cancel-secret" not in path.read_text()
    assert "RAW_SYNTHETIC_PRIVATE" not in path.read_text()


def test_visible_diagnostic_sink_failure_does_not_change_native_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class BrokenSink:
        def write(self, event: object) -> None:
            raise OSError("supporting diagnostic storage unavailable")

    invoke, _, physical = execute_fixture(
        tmp_path, monkeypatch, diagnostics=DiagnosticRouter((BrokenSink(),))
    )
    result = invoke()
    assert result.outcome.returncode == 0
    assert result.protocol.final_text == "Native final report"
    assert result.outcome.supervisor is physical[0].supervisor
    assert physical[0].supervisor.final_process_group_quiescent is True
    assert "DIAGNOSTIC_SINK_FAILED" in capsys.readouterr().err
