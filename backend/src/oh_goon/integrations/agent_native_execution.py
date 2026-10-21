"""Native CLI translation and bounded event claims for the original Step owner.

No process is launched here. The existing command runner owns the process tree,
terminal, cancellation and physical results. Native settings and agent loops
remain with the selected executable; a decoded event never admits a role result.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from oh_goon.core.agent_harness_contract import AgentHarnessObservation
from oh_goon.core.models.agent_providers import AgentProviderError, ResolvedNativeAgent

_MAX_EVENT_STREAM_BYTES = 1024 * 1024
_MAX_EVENT_RECORD_BYTES = 256 * 1024
_MAX_OBSERVATIONS = 128
_MAX_FINAL_TEXT_BYTES = 65536


@dataclass(frozen=True, slots=True)
class NativeAgentCommand:
    """Resolved native invocation; its owner opens input_file at launch time.

    Interactive stdin belongs to the native terminal. Managed inference supplies
    mandatory producer/Skill context independently at every model request.
    """

    argv: tuple[str, ...]
    input_file: Path | None
    environment: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class NativeAgentEvents:
    """Native reports only; settled is not accepted work or verification."""

    observations: tuple[AgentHarnessObservation, ...]
    final_text: str
    settled: bool
    failed: bool
    event_count: int
    omitted_observations: int = 0


def native_executable_sha256(path: Path) -> str:
    """Observe public executable bytes once with bounded, race-checked transfer."""
    if not os.access(path, os.X_OK):
        raise OSError("native program is not executable")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise OSError("native program is not a regular file")
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        after = os.fstat(stream.fileno())
    current = path.stat()

    def identity(state: os.stat_result) -> tuple[int, int, int, int, int]:
        return state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns, state.st_ctime_ns

    if identity(before) != identity(after) or identity(after) != identity(current):
        raise OSError("native executable changed during resolution")
    return digest.hexdigest()


def verify_native_agent_executable(selected: ResolvedNativeAgent) -> None:
    """Verify transported executable facts without another lookup or resolution."""
    try:
        if selected.definition.executable is None:
            raise OSError("native executable was not resolved")
        digest = native_executable_sha256(Path(selected.definition.executable))
        if digest != selected.executable_sha256:
            raise OSError("native executable changed")
    except OSError:
        raise AgentProviderError(
            "Selected native executable no longer matches its resolved identity"
        ) from None


def native_agent_command(
    selected: ResolvedNativeAgent,
    *,
    prompt_file: Path,
    workspace: Path,
    interactive: bool,
) -> NativeAgentCommand:
    """Translate a frozen selection without parsing native model configuration.

    OpenCode V2 must own a private server. Codex must not use its host daemon.
    Pi's additive system file preserves its native base prompt. Headless input
    stays file-backed rather than expanding private repository data in argv.
    """
    if selected.agent not in ("opencode", "pi", "codex"):
        raise AgentProviderError("Unsupported native command protocol")
    executable = selected.definition.executable
    if executable is None or not Path(executable).is_absolute():
        raise AgentProviderError("Native command requires an already resolved executable")
    if not prompt_file.is_absolute() or not prompt_file.is_file():
        raise AgentProviderError("Native command requires its prepared prompt file")
    if not workspace.is_absolute():
        raise AgentProviderError("Native command requires an absolute workspace")
    arguments = selected.definition.arguments
    validate_native_agent_arguments(selected.agent, arguments)
    if selected.agent == "codex" and interactive and selected.connection is None:
        raise AgentProviderError("Interactive Codex requires managed producer context delivery")
    configuration = selected.definition.configuration_directory
    environment: dict[str, str] = {}
    if selected.agent == "opencode":
        if configuration is not None:
            environment["OPENCODE_CONFIG_DIR"] = configuration
        if interactive:
            # Native configuration owns how this additional instruction source
            # composes with the user's own settings and prompt machinery.
            environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(
                {"instructions": [str(prompt_file)]}, separators=(",", ":")
            )
            argv = (executable, "--standalone", *arguments, str(workspace))
        else:
            argv = (executable, "run", "--standalone", "--format", "json", *arguments)
    elif selected.agent == "pi":
        if configuration is not None:
            environment["PI_CODING_AGENT_DIR"] = configuration
        argv = (
            executable,
            *arguments,
            *(
                ("--append-system-prompt", str(prompt_file))
                if interactive
                else ("--mode", "json", "--no-session")
            ),
        )
    else:
        if configuration is not None:
            environment["CODEX_HOME"] = configuration
        argv = (
            executable,
            "--no-daemon",
            *arguments,
            *(("exec", "--json", "--ephemeral") if not interactive else ()),
            "--cd",
            str(workspace),
            *(("-",) if not interactive else ()),
        )
    return NativeAgentCommand(
        argv=argv,
        input_file=None if interactive else prompt_file,
        environment=tuple(environment.items()),
    )


def validate_native_agent_arguments(agent: str, arguments: tuple[str, ...]) -> None:
    """Reject known transport replacements before work; native code owns other options."""
    if agent == "pi" and any(argument.partition("=")[0] == "--api-key" for argument in arguments):
        raise AgentProviderError("Native credentials must not be supplied in command arguments")
    reserved = {
        "opencode": {"--server", "--attach", "--standalone", "--no-standalone", "--dir"},
        "pi": {"--mode", "--print", "-p"},
        "codex": {"--remote", "--remote-auth-token-env", "--no-daemon", "--cd", "-C", "--worktree"},
    }[agent]
    if any(argument.partition("=")[0] in reserved for argument in arguments):
        raise AgentProviderError(
            "Native arguments cannot replace the owned terminal/server/workspace transport"
        )


def parse_native_agent_events(
    agent: str,
    output: str,
    *,
    returncode: int | None = None,
) -> NativeAgentEvents:
    """Decode a bounded batch; live sessions use NativeAgentEventParser directly."""
    parser = NativeAgentEventParser(agent)
    try:
        within_budget = (
            type(output) is str
            and len(output) <= _MAX_EVENT_STREAM_BYTES
            and len(output.encode("utf-8")) <= _MAX_EVENT_STREAM_BYTES
        )
    except UnicodeError:
        within_budget = False
    if not within_budget:
        raise AgentProviderError("Native event stream exceeds its byte budget")
    parser.feed(output)
    return parser.finish(returncode=returncode)


class NativeAgentEventParser:
    """Incremental native JSONL claims with bounded retained data.

    Native conversation length is governed by the original Step deadline. This
    decoder retains one bounded line, the current role payload and bounded
    observations, rather than the whole conversation. The command runner alone
    owns physical exit, cancellation and process lifetime.
    """

    def __init__(self, agent: str) -> None:
        if agent not in ("opencode", "pi", "codex"):
            raise AgentProviderError("Unsupported native event protocol")
        self._agent = agent
        self._pending = ""
        self._pending_bytes = 0
        self._observations: list[AgentHarnessObservation] = []
        self._omitted = 0
        self._final_text = ""
        self._final_text_bytes = 0
        self._opencode_has_text = False
        self._opencode_completed_text = False
        self._opencode_unfinished_part = False
        self._settled = False
        self._failed = False
        self._assistant_failed = False
        self._event_count = 0
        self._finished = False
        self._error: str | None = None

    def feed(self, text: str) -> None:
        """Consume decoded text; only LF frames records, including across chunks."""
        self._require_open()
        try:
            if type(text) is not str:
                raise AgentProviderError("Native event input must be decoded UTF-8 text")
            offset = 0
            while offset < len(text):
                newline = text.find("\n", offset)
                end = len(text) if newline == -1 else newline
                # Check character length before slicing/encoding an unframed
                # chunk: every UTF-8 character consumes at least one byte.
                if end - offset > _MAX_EVENT_RECORD_BYTES - self._pending_bytes:
                    raise AgentProviderError("Native event records exceed their byte bound")
                part = text[offset:end]
                try:
                    byte_count = len(part.encode("utf-8"))
                except UnicodeError:
                    raise AgentProviderError("Native event input is not valid UTF-8") from None
                if self._pending_bytes + byte_count > _MAX_EVENT_RECORD_BYTES:
                    raise AgentProviderError("Native event records exceed their byte bound")
                self._pending += part
                self._pending_bytes += byte_count
                if newline == -1:
                    return
                line = self._pending
                self._pending = ""
                self._pending_bytes = 0
                self._consume_line(line)
                offset = newline + 1
        except AgentProviderError as error:
            self._latch_error(error)
            raise

    def finish(self, returncode: int | None = None) -> NativeAgentEvents:
        """Finish at the runner's physical EOF, preserving its actual exit status."""
        self._require_open()
        try:
            if self._pending:
                line = self._pending
                self._pending = ""
                self._pending_bytes = 0
                self._consume_line(line)
        except AgentProviderError as error:
            self._latch_error(error)
            raise
        self._finished = True
        failed = self._failed or (returncode is not None and returncode != 0)
        settled = self._settled
        if self._agent == "opencode":
            # OpenCode 2.1.2 waits for session.wait(), reconciles completed
            # text, then aborts its stream. Its final step_finish can be absent.
            # Both native completion shapes require the runner's zero exit;
            # completed text alone never establishes accepted work.
            settled = (
                returncode == 0
                and not failed
                and not self._opencode_unfinished_part
                and (settled or (self._opencode_completed_text and bool(self._final_text.strip())))
            )
        return NativeAgentEvents(
            observations=tuple(self._observations),
            final_text=self._final_text,
            settled=settled,
            failed=failed,
            event_count=self._event_count,
            omitted_observations=self._omitted,
        )

    def _require_open(self) -> None:
        if self._error is not None:
            raise AgentProviderError(self._error)
        if self._finished:
            raise AgentProviderError("Native event parser has already finished")

    def _latch_error(self, error: AgentProviderError) -> None:
        # Retain only a bounded diagnostic, never an exception traceback that
        # could keep a caller's complete transport chunk alive.
        self._error = str(error)
        self._pending = ""
        self._pending_bytes = 0

    def _observe(
        self, kind: Literal["agent", "tool", "context"], name: str, identifier: object = None
    ) -> None:
        if len(self._observations) >= _MAX_OBSERVATIONS:
            self._omitted += 1
            return
        native_id = (
            identifier
            if isinstance(identifier, str) and identifier.strip() and len(identifier) <= 160
            else None
        )
        if native_id is not None:
            try:
                native_id.encode("utf-8")
            except UnicodeError:
                raise AgentProviderError("Native event identity is not valid UTF-8") from None
        self._observations.append(
            AgentHarnessObservation(
                kind=kind, summary=f"{self._agent}: {name}", native_event_id=native_id
            )
        )

    def _set_final_text(self, text: str, *, append: bool = False) -> None:
        try:
            byte_count = len(text.encode("utf-8"))
        except UnicodeError:
            raise AgentProviderError("Native final text is not valid UTF-8") from None
        separator = "\n" if append and self._opencode_has_text else ""
        size = byte_count + (self._final_text_bytes + len(separator) if append else 0)
        if size > _MAX_FINAL_TEXT_BYTES:
            raise AgentProviderError("Native final text exceeds its role-record budget")
        self._final_text = self._final_text + separator + text if append else text
        self._final_text_bytes = size

    def _consume_line(self, line: str) -> None:
        if not line.strip():
            return
        record = _event_record(line.removesuffix("\r"))
        self._event_count += 1
        event_type = record.get("type")
        if self._agent == "opencode":
            part = record.get("part")
            if event_type == "step_start":
                self._set_final_text("")
                self._opencode_has_text = False
                self._opencode_completed_text = False
                self._opencode_unfinished_part = False
                self._settled = False
                self._observe("agent", "step_start")
            elif event_type == "text" and isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    self._set_final_text(text, append=True)
                    self._opencode_has_text = True
                    time = part.get("time")
                    end = time.get("end") if isinstance(time, dict) else None
                    completed = (type(end) is int and end >= 0) or (
                        type(end) is float and math.isfinite(end) and end >= 0
                    )
                    self._opencode_completed_text = completed
                    self._opencode_unfinished_part |= not completed
            elif event_type == "step_finish":
                self._settled = True
                self._observe("agent", "step_finish")
            elif event_type == "tool_use":
                self._observe(
                    "tool", "tool_use", part.get("id") if isinstance(part, dict) else None
                )
                state = part.get("state") if isinstance(part, dict) else None
                # Native JSON CLI emits terminal tools only. An observed
                # pending/streaming tool cannot support a completion claim.
                self._opencode_unfinished_part |= not isinstance(state, dict) or state.get(
                    "status"
                ) not in ("completed", "error")
            elif event_type == "error":
                self._failed = True
                self._observe("agent", "error")
        elif self._agent == "pi":
            if event_type == "agent_start":
                self._settled = False
                self._set_final_text("")
                self._assistant_failed = False
                self._observe("agent", "agent_start")
            elif event_type == "message_end":
                message = record.get("message")
                if isinstance(message, dict) and message.get("role") == "assistant":
                    self._set_final_text(_pi_message_text(message.get("content")))
                    self._assistant_failed = message.get("stopReason") in (
                        "error",
                        "aborted",
                        "length",
                    )
            elif event_type == "agent_settled":
                self._settled = True
                self._failed |= self._assistant_failed or record.get("aborted") is True
                self._observe("agent", "agent_settled")
            elif event_type in ("tool_execution_start", "tool_execution_end"):
                self._observe("tool", event_type, record.get("toolCallId"))
            elif event_type in ("auto_compaction_start", "auto_compaction_end"):
                self._observe("context", event_type)
        else:
            if event_type == "turn.started":
                self._settled = False
                self._set_final_text("")
                self._observe("agent", "turn.started")
            elif event_type == "turn.completed":
                self._settled = True
                self._observe("agent", "turn.completed")
            elif event_type in ("turn.failed", "error"):
                self._failed = True
                self._observe("agent", "error")
            elif event_type == "item.completed":
                item = record.get("item")
                if isinstance(item, dict):
                    if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                        self._set_final_text(item["text"])
                    elif item.get("type") in ("command_execution", "mcp_tool_call", "file_change"):
                        self._observe("tool", str(item["type"]), item.get("id"))


def _event_record(line: str) -> dict[str, Any]:
    def unique_pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate event key")
            result[key] = value
        return result

    try:
        value = json.loads(
            line,
            object_pairs_hook=unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite event")),
        )
        if not isinstance(value, dict) or not isinstance(value.get("type"), str):
            raise ValueError("event must be a typed object")
        return value
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise AgentProviderError(
            "Native stdout is not a valid bounded JSONL event stream"
        ) from None


def _pi_message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part["text"]
            for part in content
            if isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        )
    return ""
