"""Owned Codex service transport inside one original native Agent Step.

The caller supplies resolved scope and frozen instructions. Native Codex owns
reasoning, tools and compaction. The supplied command owner retains every process
birth, deadline, cancellation and final process-tree closure.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from typing import TYPE_CHECKING
from uuid import uuid4

from oh_goon.core.errors import FleetPlanInvalid, OperationDeadlineExpired
from oh_goon.core.models.agent_providers import AgentProviderError, ResolvedNativeAgent
from oh_goon.core.models.fleet_observation import (
    AgentHarnessObservation,
    NativeServiceExecutionEvidence,
)
from oh_goon.core.models.fleet_plan import PLAN_CONTRACT_RETURN_CODE, PLAN_TIMEOUT_RETURN_CODE
from oh_goon.core.product_identity import stable_id
from oh_goon.engine.command import CommandCancelledError, CommandResult
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome
from oh_goon.integrations.agent_codex_service import CodexServiceClient
from oh_goon.integrations.agent_native_execution import NativeAgentEvents
from oh_goon.integrations.fleet_plan_process_isolation import native_runtime_read_only_mounts

if TYPE_CHECKING:
    from oh_goon.integrations.fleet_plan_step_execution import StepRuntime, _StepProcess

_DISABLED_FEATURES = frozenset(
    {
        "hooks",
        "plugins",
        "apps",
        "remote_plugin",
        "plugin_sharing",
        "multi_agent_v2_dynamic_tools",
        "executor_capability_discovery",
        "multi_agent_v2",
        "skill_mcp_dependency_install",
        "use_legacy_landlock",
        "multi_agent",
        "browser_use",
        "browser_use_external",
        "browser_use_full_cdp_access",
        "browser_annotation_api",
        "computer_use",
        "in_app_browser",
        "in_app_chat",
        "in_app_dictation",
        "in_app_voice",
        "in_app_local_automation",
        "in_app_updates",
        "image_generation",
        "standalone_web_search",
        "memories",
        "token_budget",
        "agent_message_board",
        "goals",
    }
)


class CodexServiceExecutionFailure(AgentProviderError):
    """Safe failed action with the original physical owner receipt intact."""

    def __init__(self, outcome: StepOutcome) -> None:
        super().__init__(outcome.stderr)
        self.outcome = outcome


def _execution_failure(
    message: str,
    physical: CommandResult | CommandCancelledError,
    argv: tuple[str, ...],
    *,
    returncode: int = PLAN_CONTRACT_RETURN_CODE,
) -> CodexServiceExecutionFailure:
    return CodexServiceExecutionFailure(
        StepOutcome(
            argv=physical.argv if isinstance(physical, CommandResult) else argv,
            returncode=returncode,
            stderr=message,
            duration_ms=physical.duration_ms,
            supervisor=physical.supervisor,
            runtime_authority=physical.runtime_authority,
            physical_execution_id=physical.physical_execution_id,
            standard_isolation=physical.standard_isolation,
        )
    )


@dataclass(frozen=True, slots=True)
class CodexServiceExecution:
    outcome: StepOutcome
    protocol: NativeAgentEvents
    execution: NativeServiceExecutionEvidence


def service_arguments(
    selected: ResolvedNativeAgent,
    *,
    repository: Path,
    read_roots: tuple[Path, ...],
    control: Path,
    scratch: Path,
    socket: Path,
    profile: str,
    read_only: bool,
    evidence_state: Path | None = None,
) -> tuple[str, ...]:
    """Materialize the caller's exact public native policy, never native files."""
    connection = selected.profile.codex_service
    if connection is None or selected.definition.executable is None:
        raise FleetPlanInvalid("Codex service requires its resolved native selection")
    filesystem = {
        ":root": "deny",
        ":minimal": "read",
        **{str(root): "read" for root in read_roots},
        str(repository): "read" if read_only else "write",
        str(repository / ".git"): "read",
        # Native Codex reserves this directory read-only, even when absent.
        # A subtree deny avoids its Linux missing-directory file placeholder
        # collision and also denies native helper reads of configuration files.
        str(repository / ".codex"): "read",
        str(repository / ".codex" / "**"): "deny",
        str(control): "read",
        str(socket.parent): "deny",
        str(scratch): "write",
    }
    if evidence_state is not None:
        filesystem[str(evidence_state)] = "write"
    rules = ",".join(json.dumps(path) + "=" + json.dumps(mode) for path, mode in filesystem.items())
    settings = [
        f"default_permissions={json.dumps(profile)}",
        f"permissions.{profile}={{filesystem={{{rules}}},network={{enabled=false}}}}",
        'approval_policy="never"',
        "allow_login_shell=false",
        "notify=[]",
        'web_search="disabled"',
        "cloud.skills.enabled=false",
        'shell_environment_policy.inherit="none"',
        'shell_environment_policy.include_only=["PATH","TMPDIR"]',
        "shell_environment_policy.experimental_use_profile=false",
        'shell_environment_policy.set={PATH="/usr/local/bin:/usr/bin:/bin",TMPDIR='
        + json.dumps(str(scratch))
        + "}",
        *("features." + name + "=false" for name in sorted(_DISABLED_FEATURES)),
    ]
    return (
        selected.definition.executable,
        *(part for setting in settings for part in ("-c", setting)),
        "app-server",
        "--listen",
        "unix://" + str(socket),
    )


async def _turn(
    client: CodexServiceClient,
    thread: str,
    turn: str,
    runtime: StepRuntime,
    deadline: float,
    worker: Thread,
) -> NativeAgentEvents:
    observations: list[AgentHarnessObservation] = []
    count = 0
    omitted = 0
    final = ""
    while True:
        if runtime.cancel_requested is not None and runtime.cancel_requested():
            raise OperationCancelledError("Codex service Run was cancelled")
        if time.monotonic() >= deadline:
            raise OperationDeadlineExpired("Codex service Step deadline expired")
        if not worker.is_alive():
            raise AgentProviderError("Owned Codex service stopped before terminal evidence")
        try:
            async with asyncio.timeout(min(0.25, deadline - time.monotonic())):
                message = await client.next_notification(thread)
        except TimeoutError:
            continue
        count += 1
        params = message.get("params", {})
        if not isinstance(params, dict):
            raise AgentProviderError("Codex service returned invalid scoped progress")
        method = message.get("method")
        if method == "item/completed":
            if params.get("turnId") != turn:
                raise AgentProviderError("Codex service progress changed the owned turn")
            item = params.get("item")
            if not isinstance(item, dict):
                raise AgentProviderError("Codex service returned invalid item evidence")
            kind = item.get("type")
            if kind == "agentMessage":
                text = item.get("text")
                if not isinstance(text, str) or len(text.encode("utf-8")) > 65536:
                    raise AgentProviderError("Codex service final text exceeds its bound")
                final = text
            if kind in {"agentMessage", "commandExecution", "contextCompaction"}:
                if len(observations) >= 128:
                    omitted += 1
                    continue
                observations.append(
                    AgentHarnessObservation(
                        kind="context"
                        if kind == "contextCompaction"
                        else "agent"
                        if kind == "agentMessage"
                        else "tool",
                        summary="Native Codex completed " + str(kind),
                    )
                )
        if method == "turn/completed":
            terminal = params.get("turn")
            if not isinstance(terminal, dict) or terminal.get("id") != turn:
                raise AgentProviderError("Codex service terminal evidence changed the owned turn")
            if terminal.get("status") != "completed" or terminal.get("error") is not None:
                raise AgentProviderError("Codex service turn did not complete successfully")
            return NativeAgentEvents(tuple(observations), final, True, False, count, omitted)


def execute_codex_service_run(
    selected: ResolvedNativeAgent,
    *,
    repository: Path,
    instructions: bytes,
    task: str,
    read_roots: tuple[Path, ...],
    control: Path,
    process: _StepProcess,
    runtime: StepRuntime,
    deadline: float,
    read_only: bool,
    evidence_state: Path | None = None,
) -> CodexServiceExecution:
    """Execute one native turn, then close the owned process before reporting it."""
    connection = selected.profile.codex_service
    if connection is None:
        raise FleetPlanInvalid("Codex service requires its resolved native selection")
    # Reuse the original runtime/mount owner, including uv's lexical Python
    # aliases. A native permission path needs the same source and target paths;
    # this does not discover another installation or widen to the host home.
    runtime_mounts = native_runtime_read_only_mounts(repository, selected)
    read_roots = tuple(
        dict.fromkeys(
            (
                *read_roots,
                *(mount.source for mount in runtime_mounts),
                *(Path(mount.target) for mount in runtime_mounts),
            )
        )
    )
    private = control / "service-private"
    scratch = control / "service-scratch"
    private.mkdir(mode=0o700)
    scratch.mkdir(mode=0o700)
    socket = private / "agent.sock"
    profile = "oh_goon_" + uuid4().hex
    argv = service_arguments(
        selected,
        repository=repository,
        read_roots=read_roots,
        control=control,
        scratch=scratch,
        socket=socket,
        profile=profile,
        read_only=read_only,
        evidence_state=evidence_state,
    )
    stop = Event()
    results: list[CommandResult | BaseException] = []
    protocol: NativeAgentEvents | None = None
    primary: BaseException | None = None
    # A new owned service must not adopt its parent's desktop/CLI conversation,
    # tool IPC or user-agent overrides. Native home/provider authentication stays
    # with the native child; none of this environment becomes a tool environment.
    parent_controls = {
        "CODEX_VERSION",
        "CODEX_THREAD_ID",
        "CODEX_SESSION_ID",
        "CODEX_PERMISSION_PROFILE",
        "CODEX_TOOL_CALL_ID",
    }
    environment = {
        name: value
        for name, value in process.host_environment.items()
        if name not in parent_controls
        and not name.startswith(
            ("CODEX_INTERNAL_", "CODEX_APP_", "CODEX_EXEC_SERVER_", "NODE_REPL_")
        )
    }
    environment["TMPDIR"] = str(scratch)
    environment["CODEX_HOME"] = connection.home
    version = process.command_runner(
        selected.definition.executable or "",
        "--version",
        cwd=repository,
        env=environment,
        timeout=min(5, max(0.001, deadline - time.monotonic())),
        cancel_requested=runtime.cancel_requested,
        input_data=b"",
    )
    if (
        version.returncode != 0
        or version.stdout.strip() != "codex-cli 0.163.0"
        or not version.supervisor.final_process_group_quiescent
    ):
        raise _execution_failure("Codex service version has not been qualified", version, argv)

    def serve() -> None:
        try:
            results.append(
                process.command_runner(
                    *argv,
                    cwd=repository,
                    env=environment,
                    timeout=max(0.001, deadline - time.monotonic()),
                    cancel_requested=lambda: stop.is_set()
                    or (runtime.cancel_requested is not None and runtime.cancel_requested()),
                    input_data=b"",
                    retain_output_tail=True,
                )
            )
        except BaseException as error:
            results.append(error)

    (private / "canary").write_text("synthetic private boundary canary")
    (control / "read-only-canary").write_text("synthetic read-only boundary canary")
    worker = Thread(target=serve, name="original-step-codex-service")
    worker.start()

    async def execute() -> NativeAgentEvents:
        while not socket.exists():
            if runtime.cancel_requested is not None and runtime.cancel_requested():
                raise OperationCancelledError("Codex service cancelled before readiness")
            if not worker.is_alive():
                raise AgentProviderError("Owned Codex service failed before readiness")
            if time.monotonic() >= deadline:
                raise OperationDeadlineExpired("Codex service readiness deadline expired")
            await asyncio.sleep(0.02)
        async with CodexServiceClient(
            socket,
            timeout_seconds=min(10, max(0.001, deadline - time.monotonic())),
            experimental_api=True,
        ) as client:
            client.require_native_home_separation(
                (
                    *read_roots,
                    repository,
                    control,
                    scratch,
                    *((evidence_state,) if evidence_state is not None else ()),
                ),
                expected_home=Path(connection.home),
            )
            created = await client.start_thread(
                cwd=repository,
                permissions=profile,
                model=connection.model,
                developer_instructions=instructions.decode("utf-8"),
                ephemeral=True,
                configuration={"features.incremental_tools": False},
            )
            thread = created["thread"]["id"]
            await client.require_no_mcp_servers(thread)
            flags = await client.feature_flags(thread)
            if any(flags.get(name) is not False for name in _DISABLED_FEATURES):
                raise AgentProviderError(
                    "Codex service tool capability exclusion was not established"
                )
            qualification = await client.command_exec(
                (
                    sys.executable,
                    "-I",
                    "-c",
                    "import pathlib,sys; p=pathlib.Path(sys.argv[1]);\ntry: p.read_bytes()\nexcept OSError: pass\nelse: sys.exit(1)\np=pathlib.Path(sys.argv[2]);p.write_text('qualification');p.unlink()\np=pathlib.Path(sys.argv[3]);\ntry: p.write_text('unauthorized')\nexcept OSError: pass\nelse: sys.exit(2)",
                    str(private / "canary"),
                    str(scratch / "canary"),
                    str(control / "read-only-canary"),
                ),
                cwd=repository,
                permissions=profile,
                timeout_seconds=min(5, max(0.001, deadline - time.monotonic())),
            )
            qualification_exit_code = qualification.get("exitCode")
            if type(qualification_exit_code) is not int or qualification_exit_code != 0:
                raise AgentProviderError("Codex service native sandbox qualification failed")
            turn = await client.start_turn(
                thread,
                task,
                permissions=profile,
                effort=connection.reasoning_effort,
            )
            started_turn = turn.get("turn")
            turn_id = started_turn.get("id") if isinstance(started_turn, dict) else None
            if (
                type(turn_id) is not str
                or not turn_id.strip()
                or len(turn_id) > 256
                or not turn_id.isprintable()
            ):
                raise AgentProviderError("Codex service did not identify its owned turn")
            return await _turn(client, thread, turn_id, runtime, deadline, worker)

    try:

        async def bounded_execute() -> NativeAgentEvents:
            try:
                async with asyncio.timeout(max(0.001, deadline - time.monotonic())):
                    return await execute()
            except TimeoutError:
                raise OperationDeadlineExpired("Codex service Step deadline expired") from None

        protocol = asyncio.run(bounded_execute())
    except BaseException as error:
        primary = error
    finally:
        stop.set()
        worker.join(timeout=12)
    if worker.is_alive() or len(results) != 1:
        raise FleetPlanInvalid("Codex service process cleanup remains unverified")
    physical = results[0]
    if not isinstance(physical, (CommandResult, CommandCancelledError)):
        if isinstance(physical, BaseException):
            if hasattr(physical, "stdout"):
                physical.stdout = ""
            if hasattr(physical, "stderr"):
                physical.stderr = ""
            raise physical
        raise FleetPlanInvalid("Codex service process evidence is missing")
    if not physical.supervisor.final_process_group_quiescent:
        raise _execution_failure("Codex service process cleanup remains unverified", physical, argv)
    if runtime.cancel_requested is not None and runtime.cancel_requested():
        raise OperationCancelledError("Codex service Run was cancelled") from (
            physical if isinstance(physical, BaseException) else None
        )
    if isinstance(primary, (AgentProviderError, OperationDeadlineExpired)):
        raise _execution_failure(
            str(primary),
            physical,
            argv,
            returncode=PLAN_TIMEOUT_RETURN_CODE
            if isinstance(primary, OperationDeadlineExpired)
            else PLAN_CONTRACT_RETURN_CODE,
        ) from None
    if primary is not None:
        raise primary
    if protocol is None or not isinstance(physical, CommandCancelledError):
        raise _execution_failure(
            "Codex service lacks terminal turn and owned shutdown evidence", physical, argv
        )
    # The native turn result and the daemon's requested shutdown are distinct.
    # Keep the ACTUAL cancelled-daemon receipt, without synthesizing a command
    # success or claiming that a protocol ACK stopped native tools.
    outcome = StepOutcome(
        argv=(selected.definition.executable or "", "app-server"),
        returncode=0,
        stdout=protocol.final_text,
        duration_ms=physical.duration_ms,
        supervisor=physical.supervisor,
        runtime_authority=physical.runtime_authority,
        physical_execution_id=physical.physical_execution_id,
        standard_isolation=physical.standard_isolation,
    )
    execution = NativeServiceExecutionEvidence(
        configuration_id=stable_id(
            "codex-native-service-configuration",
            {
                "argv": argv,
                "executable_sha256": selected.executable_sha256,
                "native_home": connection.home,
            },
        ),
        native_version=version.stdout.strip().removeprefix("codex-cli "),
        permission_profile_id=profile,
        repository_access="read_only" if read_only else "write",
        disabled_features=tuple(sorted(_DISABLED_FEATURES)),
    )
    return CodexServiceExecution(outcome, protocol, execution)
