"""Native agent CLI ingress through the original Fleet Run lifecycle."""

from __future__ import annotations

import argparse
import json
import os
import select
import shlex
import shutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from functools import partial
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Thread
from typing import TYPE_CHECKING

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import FleetDomainError
from oh_goon.core.models.agent_providers import (
    DEFAULT_NATIVE_AGENT,
    AgentProviderError,
    AgentProvidersConfig,
    ResolvedNativeAgent,
)
from oh_goon.core.vocabulary import FleetRunOutcome, GameTapeStatus
from oh_goon.runtime.agent_providers import (
    agent_providers_path,
    load_agent_providers,
    resolve_native_agent,
)

if TYPE_CHECKING:
    from oh_goon.core.models.run import FleetRunDetail
    from oh_goon.engine.command import CommandTerminal
    from oh_goon.engine.git import GitService
    from oh_goon.integrations.fleet_plan_process_isolation import ProcessIsolationBackend

_MAX_TASK_BYTES = 65536
_MAX_TERMINAL_CHUNK_BYTES = 65536
_MAX_FAILURE_STDERR_BYTES = 16384


def add_ai_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser("ai", help="Run a native coding agent through a managed Run")
    parser.add_argument(
        "--agent",
        default=DEFAULT_NATIVE_AGENT,
        choices=("opencode", "pi", "codex"),
        help="Native coding agent (default: codex)",
    )
    parser.add_argument(
        "--profile", help="Native execution profile; otherwise use the catalog default"
    )
    parser.add_argument("--workspace", type=Path, default=Path(), help="Git repository root")
    parser.add_argument("--skill", type=Path, help="Caller-selected Skill source")
    parser.add_argument(
        "--handoff-from",
        metavar="RUN_ID",
        help="Seed a fresh native session from one explicit durable Run handoff as untrusted context",
    )
    parser.add_argument("--config", type=Path, help="Provider/native execution catalog")
    prompt = parser.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--prompt", help="Task for the selected native agent")
    prompt.add_argument("--prompt-file", type=Path, help="UTF-8 task file, or - for stdin")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--headless", action="store_true", help="Use native JSON event output")
    mode.add_argument("--interactive", action="store_true", help="Require a native terminal UI")
    parser.add_argument(
        "--verify", action="append", default=[], help="Native command, without a shell; repeatable"
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json", help="Headless terminal Run detail"
    )


def _task(args: argparse.Namespace) -> str:
    from oh_goon.core.models.fleet_plan import MAX_AGENT_TASK_CHARS

    data: bytes
    if args.prompt is not None:
        data = args.prompt.encode("utf-8")
    elif args.prompt_file == Path("-"):
        data = sys.stdin.buffer.read(_MAX_TASK_BYTES + 1)
    else:
        with args.prompt_file.expanduser().open("rb") as stream:
            data = stream.read(_MAX_TASK_BYTES + 1)
    if len(data) > _MAX_TASK_BYTES:
        raise AgentProviderError("Native task exceeds 65536 UTF-8 bytes")
    text = data.decode("utf-8")
    if not text.strip() or "\x00" in text:
        raise AgentProviderError("Native task must be nonblank UTF-8 text without NUL")
    if len(text) > MAX_AGENT_TASK_CHARS:
        raise AgentProviderError(
            f"Native task exceeds {MAX_AGENT_TASK_CHARS} authored characters; use Skill resources for larger context"
        )
    return text


def _interactive(args: argparse.Namespace) -> bool | None:
    terminal = sys.stdin.isatty() and sys.stdout.isatty()
    if args.interactive and (args.as_json or not terminal or os.name == "nt"):
        raise AgentProviderError("Interactive mode requires a POSIX terminal and cannot use --json")
    if args.interactive:
        return True
    return (
        None if terminal and os.name != "nt" and not args.headless and not args.as_json else False
    )


def _isolation(selected: ResolvedNativeAgent) -> ProcessIsolationBackend | None:
    from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation

    caps = selected.profile.resources
    requested = any(
        value is not None for value in (caps.cpu_millis, caps.memory_mib, caps.max_processes)
    )
    if selected.profile.backend == "local":
        if requested:
            raise AgentProviderError("Resource caps require a native isolated execution profile")
        return None
    executable = shutil.which("bwrap")
    if executable is None:
        raise AgentProviderError(
            "The selected isolated profile requires installed Bubblewrap (bwrap)"
        )
    resource_executable = shutil.which("systemd-run") if requested else None
    resource_query_executable = shutil.which("systemctl") if requested else None
    if requested and resource_executable is None:
        raise AgentProviderError("Selected resource caps require installed systemd-run")
    if requested and resource_query_executable is None:
        raise AgentProviderError("Selected resource caps require installed systemctl")
    return BubblewrapProcessIsolation(
        executable,
        standard_resource_executable=resource_executable,
        standard_resource_query_executable=resource_query_executable,
    )


def resolve_native_cli_selection(
    *,
    agent: str,
    profile: str | None,
    workspace: Path,
    config_file: Path | None,
    interactive: bool | None = False,
) -> tuple[ResolvedNativeAgent, ProcessIsolationBackend | None, GitService]:
    """Resolve a single catalog and native workload before application mutation."""
    from oh_goon.engine.git import GitService
    from oh_goon.integrations.fleet_plan_native_steps import require_native_agent_compatibility
    from oh_goon.integrations.fleet_plan_process_isolation import native_profile_command_runner
    from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority

    config_path = agent_providers_path(os.environ, config_file)
    config = (
        load_agent_providers(config_path)
        if config_file is not None or config_path.exists()
        else AgentProvidersConfig()
    )
    selected = resolve_native_agent(
        config, agent, profile, workspace=workspace, environment=os.environ
    )
    if interactive is None:
        interactive = (
            selected.profile.codex_login is None and selected.profile.codex_service is None
        )
    selected = replace(selected, interactive=interactive)
    require_native_agent_compatibility(selected, terminal_available=interactive)
    isolation = _isolation(selected)
    if isolation is not None:
        selected = replace(selected, backend_configuration_id=isolation.configuration_identity())
    command_runner = native_profile_command_runner(
        workspace, selected, isolation, RepositoryWorkspaceAuthority.pin_repository_subject
    )
    git = GitService(
        command_runner=command_runner,
        observation_runner=partial(command_runner, read_only_workspace=True)
        if isolation is not None
        else None,
    )
    if git.root(workspace) != workspace:
        raise AgentProviderError("Native workspace must be an existing Git repository root")
    return selected, isolation, git


def native_plan_selection(
    args: argparse.Namespace,
) -> tuple[ResolvedNativeAgent, ProcessIsolationBackend | None, GitService]:
    """One native selection for lock, Plan execution and bounded repair ingress."""
    if args.native_workspace is None or getattr(args, "agent_model_profile", None):
        raise AgentProviderError(
            "Native selection requires an explicit registered repository workspace and retains native model settings"
        )
    workspace = args.native_workspace.expanduser().resolve(strict=True)
    args.native_workspace = workspace
    return resolve_native_cli_selection(
        agent=args.native_agent,
        profile=args.native_profile,
        workspace=workspace,
        config_file=args.agent_providers_file,
    )


def run_ai_command(args: argparse.Namespace) -> int:
    try:
        interactive = _interactive(args)
        task = _task(args)
        workspace = args.workspace.expanduser().resolve(strict=True)
        skill = args.skill.expanduser().resolve(strict=True) if args.skill is not None else None
        verification = tuple(tuple(shlex.split(command)) for command in args.verify)
        if any(
            not command or any("\x00" in argument for argument in command)
            for command in verification
        ):
            raise AgentProviderError("Verification must name a native command without NUL")
        selected, isolation, git = resolve_native_cli_selection(
            agent=args.agent,
            profile=args.profile,
            workspace=workspace,
            config_file=args.config,
            interactive=interactive,
        )
        interactive = selected.interactive
        with _host_terminal() if interactive else nullcontext(None) as terminal:
            runtime = ApplicationRuntime.build(
                standard_local=True,
                terminal=terminal,
                standard_process_isolation=isolation,
                git_service=git,
            )
            result, detail = _run_and_close(
                runtime,
                selected,
                workspace,
                skill,
                task,
                verification,
                handoff_from=args.handoff_from,
            )
        if args.as_json:
            print(detail.model_dump_json(indent=2))
        else:
            print(f"Status: {detail.run.status.value}")
            print(f"Outcome: {detail.outcome.value}")
        return result
    except KeyboardInterrupt:
        print("Native run interrupted during setup.", file=sys.stderr)
        return 130
    except (FleetDomainError, OSError, ValueError, UnicodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _run_and_close(
    runtime: ApplicationRuntime,
    selected: ResolvedNativeAgent,
    workspace: Path,
    skill: Path | None,
    task: str,
    verification: tuple[tuple[str, ...], ...],
    *,
    handoff_from: str | None = None,
) -> tuple[int, FleetRunDetail]:
    from oh_goon.cli import _shutdown_after_completed_command, _shutdown_after_primary_failure

    try:
        result, detail = _run_selected(
            runtime,
            selected,
            workspace,
            skill,
            task,
            verification,
            handoff_from=handoff_from,
        )
        if result not in (0, 130):
            _print_failure_stderr(runtime, detail)
    except BaseException:
        _shutdown_after_primary_failure(runtime)
        raise
    cleanup = _shutdown_after_completed_command(runtime)
    if cleanup is not None:
        print(f"Native Run cleanup failed: {cleanup[1]}", file=sys.stderr)
        result = result if result else 2
    return result, detail


def _print_failure_stderr(runtime: ApplicationRuntime, detail: FleetRunDetail) -> None:
    """Present a bounded stderr tail before its original transient owner closes."""
    try:
        batch = runtime.runs.game_tape_live_output(detail.run.fleet_run_id)
    except (FleetDomainError, OSError, ValueError):
        print("Run failure stderr is unavailable from Game Tape live output.", file=sys.stderr)
        _print_recorded_failure(detail)
        return
    remaining = _MAX_FAILURE_STDERR_BYTES
    parts: list[str] = []
    omitted = batch.dropped_chunk_count > 0
    for chunk in reversed(batch.chunks):
        if chunk.stream != "stderr":
            continue
        encoded = chunk.text.encode("utf-8")
        if len(encoded) > remaining:
            if remaining:
                parts.append(encoded[-remaining:].decode("utf-8", errors="ignore"))
            omitted = True
            break
        parts.append(chunk.text)
        remaining -= len(encoded)
    text = "".join(reversed(parts))
    if text:
        print("Run failure stderr (transient tail):", file=sys.stderr)
        if omitted:
            print("... earlier output omitted ...", file=sys.stderr)
        print(text, file=sys.stderr, end="" if text.endswith("\n") else "\n")
    elif not _print_recorded_failure(detail):
        print("Run failure has no retained stderr; inspect the Fleet Run ID.", file=sys.stderr)


def _print_recorded_failure(detail: FleetRunDetail) -> bool:
    """Show recorded Step outcomes and bounded claims without source/model output."""
    printed = 0
    for repository in detail.game_tape.repositories:
        for step in repository.steps:
            if step.status is not GameTapeStatus.FAILED:
                continue
            print(f"Recorded Step failure: {step.step_id} (exit {step.exit_code})", file=sys.stderr)
            printed += 1
            if printed == 8:
                return True
            for observation in step.native_agent_observations:
                if not observation.failed:
                    continue
                for event in reversed(observation.events):
                    if event.kind != "agent":
                        continue
                    summary = event.summary.encode("utf-8")[:512].decode("utf-8", errors="ignore")
                    print(
                        f"Native protocol claim (Step exit {step.exit_code}): {summary}",
                        file=sys.stderr,
                    )
                    printed += 1
                    if printed == 8:
                        return True
    return printed > 0


def _run_selected(
    runtime: ApplicationRuntime,
    selected: ResolvedNativeAgent,
    workspace: Path,
    skill: Path | None,
    task: str,
    verification: tuple[tuple[str, ...], ...],
    *,
    handoff_from: str | None = None,
) -> tuple[int, FleetRunDetail]:
    from oh_goon.cli_plan_run_readback import run_handoff_projection
    from oh_goon.services.fleet_plans import _native_prior_context

    admitted = None
    try:
        prior_context = None
        if handoff_from is not None:
            prior_context = json.dumps(
                run_handoff_projection(runtime.runs.get(handoff_from)),
                ensure_ascii=True,
                separators=(",", ":"),
            )
            _native_prior_context(prior_context)  # reject oversize before any new Run effect
        if any(
            Path(entry["path"]).resolve() == workspace
            for entry in runtime.repositories.exclusions()
        ):
            raise AgentProviderError(
                "Selected repository is excluded; include it explicitly before running"
            )
        if not any(workspace.is_relative_to(root) for root in runtime.locations.list()):
            runtime.locations.add(workspace)
        admitted = runtime.plans.run_native_agent(
            selected=selected,
            workspace=workspace,
            skill=skill,
            task=task,
            verification=verification,
            **({"prior_context": prior_context} if prior_context is not None else {}),
        )
        print(f"Fleet Run ID: {admitted.fleet_run_id}", file=sys.stderr)
        _print_handoff_reference(admitted.fleet_run_id)
        detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id)
    except KeyboardInterrupt:
        if admitted is None:
            raise
        runtime.runs.cancel(admitted.fleet_run_id)
        detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id)
        print(
            f"Invocation interrupted; cancellation requested: {admitted.fleet_run_id}; "
            f"execution status: {detail.run.status.value}",
            file=sys.stderr,
        )
        print("Cleanup evidence: inspect durable Run handoff.", file=sys.stderr)
        return 130, detail
    return (0 if detail.outcome is FleetRunOutcome.SUCCESS else 1), detail


def _print_handoff_reference(fleet_run_id: str) -> None:
    argv = ["oh-goon", "plan", "handoff", fleet_run_id, "--json"]
    print("Durable Run handoff argv: " + json.dumps(argv), file=sys.stderr)


@contextmanager
def _host_terminal() -> Iterator[CommandTerminal]:
    import fcntl
    import termios
    import tty

    from oh_goon.engine.command import CommandTerminal

    input_fd, output_fd = sys.stdin.fileno(), sys.stdout.fileno()
    original = termios.tcgetattr(input_fd)
    input_flags = fcntl.fcntl(input_fd, fcntl.F_GETFL)
    output_flags = fcntl.fcntl(output_fd, fcntl.F_GETFL)
    output: Queue[bytes] = Queue(maxsize=64)
    closing, abandon = Event(), Event()
    output_errors: list[Exception] = []

    def size() -> tuple[int, int]:
        dimensions = os.get_terminal_size(output_fd)
        return dimensions.lines or 24, dimensions.columns or 80

    def read_input(limit: int) -> bytes | None:
        if not select.select([input_fd], [], [], 0)[0]:
            return None
        try:
            return os.read(input_fd, limit)
        except BlockingIOError:
            return None

    def write_output(data: bytes) -> None:
        if output_errors:
            raise OSError("User terminal output disconnected") from output_errors[0]
        if len(data) > _MAX_TERMINAL_CHUNK_BYTES:
            raise OSError("User terminal output exceeds its chunk bound")
        try:
            output.put_nowait(data)
        except Full:
            raise OSError("User terminal output exceeds its bounded transport queue") from None

    def drain_output() -> None:
        remaining = memoryview(b"")
        try:
            while not abandon.is_set():
                if not remaining:
                    try:
                        remaining = memoryview(output.get(timeout=0.05))
                    except Empty:
                        if closing.is_set():
                            return
                        continue
                if not select.select([], [output_fd], [], 0.05)[1]:
                    continue
                try:
                    written = os.write(output_fd, remaining)
                except BlockingIOError:
                    continue
                if written <= 0:
                    raise OSError("User terminal output disconnected")
                remaining = remaining[written:]
        except Exception as exc:
            output_errors.append(exc)

    writer = Thread(target=drain_output, name="oh-goon-user-terminal-output", daemon=True)
    writer_started = False

    try:
        tty.setraw(input_fd, when=termios.TCSANOW)
        attributes = termios.tcgetattr(input_fd)
        attributes[3] |= termios.ISIG
        termios.tcsetattr(input_fd, termios.TCSANOW, attributes)
        fcntl.fcntl(input_fd, fcntl.F_SETFL, input_flags | os.O_NONBLOCK)
        fcntl.fcntl(output_fd, fcntl.F_SETFL, output_flags | os.O_NONBLOCK)
        writer.start()
        writer_started = True
        yield CommandTerminal(
            read_input=read_input, write_output=write_output, initial_size=size(), current_size=size
        )
    finally:
        primary_failure = sys.exc_info()[0] is not None
        closing.set()
        if writer_started:
            writer.join(timeout=5)
            if writer.is_alive():
                abandon.set()
                writer.join(timeout=1)
                output_errors.append(OSError("User terminal output could not drain before close"))
        try:
            fcntl.fcntl(output_fd, fcntl.F_SETFL, output_flags)
        finally:
            try:
                fcntl.fcntl(input_fd, fcntl.F_SETFL, input_flags)
            finally:
                termios.tcsetattr(input_fd, termios.TCSANOW, original)
        if output_errors:
            message = "Native terminal transport failed while delivering output"
            if primary_failure:
                print(message, file=sys.stderr)
            else:
                raise OSError(message) from output_errors[0]


def main(argv: list[str] | None = None) -> int:
    from oh_goon.cli import main as cli_main

    return cli_main(["ai", *(sys.argv[1:] if argv is None else argv)])
