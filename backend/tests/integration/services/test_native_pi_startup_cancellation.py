"""Installed Pi extension and MCP startup remain inside the claimed Run worker."""

from __future__ import annotations

import json
import os
import select
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_ai import resolve_native_cli_selection
from oh_goon.core.models.agent_providers import PiSessionIntent
from oh_goon.core.models.run_events import (
    DescendantsQuiescentEvent,
    ProcessExitedEvent,
    TerminationRequestedEvent,
)
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import FleetRunOutcome, FleetRunStatus, GameTapeStatus
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.journal import Journal
from tests.fakes.native_lifetime import bind_native_descendant, wait_for_native_step_process

_SLOW_INITIALIZE = """\
import json
import os
import subprocess
import sys

line = sys.stdin.buffer.readline(65537)
assert 0 < len(line) <= 65536
request = json.loads(line)
assert request["method"] == "initialize"

# The harmless helper represents delayed initialization. Its birth is the
# test's signal that Pi really started this configured server and sent MCP
# initialize. It inherits normal process custody and produces no output.
subprocess.run(
    [
        sys.executable,
        "-I",
        "-c",
        "from time import sleep; sleep(25)",
        os.environ["OH_GOON_MCP_READY_MARKER"],
    ],
    check=True,
    timeout=30,
)
print(
    json.dumps({
        "jsonrpc": "2.0",
        "id": request["id"],
        "result": {
            "protocolVersion": request["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "delayed-fixture", "version": "1"},
        },
    }),
    flush=True,
)
"""


def _slow_extension(marker: str) -> str:
    return f"""\
import {{spawn}} from "node:child_process";

export default function (pi) {{
  pi.on("session_start", async () => {{
    await new Promise((resolve, reject) => {{
      const child = spawn(
        "/usr/bin/python3",
        ["-I", "-c", "from time import sleep; sleep(25)", {json.dumps(marker)}],
        {{stdio: "ignore"}},
      );
      child.once("error", reject);
      child.once("exit", (code) => {{
        if (code === 0) resolve();
        else reject(new Error("Controlled startup helper stopped"));
      }});
    }});
  }});
}}
"""


@pytest.mark.certification
@pytest.mark.parametrize("startup", ["mcp", "extension"])
@pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bwrap") is None,
    reason="Linux Bubblewrap and pidfds required",
)
def test_installed_pi_cancel_retires_an_inflight_native_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, startup: Literal["mcp", "extension"]
) -> None:
    executable = os.environ.get("OH_GOON_NATIVE_PI")
    runtime_root = os.environ.get("OH_GOON_NATIVE_PI_RUNTIME_ROOT")
    if not executable or not runtime_root:
        pytest.skip("Select the installed Pi executable and public runtime root")
    program = Path(executable).resolve(strict=True)
    public_runtime = Path(runtime_root).resolve(strict=True)
    assert public_runtime.is_dir() and program.is_relative_to(public_runtime)
    database_path = tmp_path / "state/app.sqlite"
    monkeypatch.setenv("OH_GOON_DB", str(database_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "fixture-home"))
    version = subprocess.run(
        (str(program), "--version"), capture_output=True, text=True, timeout=10, check=True
    ).stdout.strip()
    assert version == "1.3.0", "Requalify extension and MCP startup for this Pi release"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "source.py"
    source.write_text("VALUE = 42\n", encoding="utf-8")
    subprocess.run(("git", "init", "-q", str(workspace)), check=True, timeout=10)
    configuration = tmp_path / "public-pi"
    configuration.mkdir()
    marker = f"delayed-{startup}-startup-" + uuid4().hex
    if startup == "mcp":
        (configuration / "delayed_mcp.py").write_text(_SLOW_INITIALIZE, encoding="utf-8")
        (configuration / "mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "delayed": {
                            "command": "/usr/bin/python3",
                            "args": ["-I", "/config/native/delayed_mcp.py"],
                            "env": {"OH_GOON_MCP_READY_MARKER": marker},
                            "exposure": "direct",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        extension = "builtin:mcp"
    else:
        (configuration / "startup.mjs").write_text(_slow_extension(marker), encoding="utf-8")
        extension = "/config/native/startup.mjs"
    for name, payload in (
        ("settings.json", {"defaultProvider": "approved", "defaultModel": "mcp-cancel-fixture"}),
        (
            "models.json",
            {
                "providers": {
                    "approved": {
                        "baseUrl": "http://127.0.0.1:18743/v1",
                        "api": "openai-completions",
                        "apiKey": "oh-goon-local",
                        "models": [{"id": "mcp-cancel-fixture"}],
                    }
                }
            },
        ),
    ):
        (configuration / name).write_text(json.dumps(payload), encoding="utf-8")
    original_config = {entry.name: entry.read_bytes() for entry in configuration.iterdir()}
    with socket.socket() as provider_listener:
        provider_listener.bind(("127.0.0.1", 0))
        provider_listener.listen(1)
        catalog = tmp_path / "providers.json"
        catalog.write_text(
            json.dumps(
                {
                    "providers": {
                        "fixture": {
                            "api": "openai-chat-completions",
                            "base_url": f"http://127.0.0.1:{provider_listener.getsockname()[1]}/v1",
                        }
                    },
                    "models": {"fixture": {"provider": "fixture", "model": "mcp-cancel-fixture"}},
                    "native_profiles": {
                        "isolated": {
                            "backend": "bubblewrap",
                            "allowed_agents": ["pi"],
                            "inference_profile": "fixture",
                            "pi_rpc": True,
                            "timeout_seconds": 45,
                        }
                    },
                    "native_agents": {
                        "pi": {
                            "executable": str(program),
                            "runtime_roots": [str(public_runtime)],
                            "configuration_directory": str(configuration),
                            "arguments": ["--no-extensions", "--extension", extension],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        selected, backend, git = resolve_native_cli_selection(
            agent="pi",
            profile="isolated",
            workspace=workspace,
            config_file=catalog,
            session=PiSessionIntent(mode="ephemeral"),
        )
        runtime = ApplicationRuntime.build(
            standard_local=True, standard_process_isolation=backend, git_service=git
        )
        try:
            runtime.locations.add(workspace)
            admitted = runtime.plans.run_native_agent(
                selected=selected,
                workspace=workspace,
                skill=None,
                task="Review the fixture source without changing it.",
                role="review",
            )
            run_id = admitted.fleet_run_id
            assert isinstance(admitted.target, FleetPlanTarget)
            step_id = admitted.target.plan.steps[0].step_id
            original = wait_for_native_step_process(
                lambda: runtime.runs.events(run_id).events, step_id, program
            )
            observation_database = LocalDatabase(database_path, read_only=True)
            with bind_native_descendant(original, marker) as process:
                with observation_database.transaction() as connection:
                    lease = ExecutionCoordinator(observation_database).active_fleet_lease_in(
                        connection, run_id
                    )
                assert lease is not None and lease.custody is not None
                assert lease.custody.phase == "WORKER_OWNED"
                assert lease.custody.owner_pid != os.getpid()
                assert process.parent.pid != process.child.pid
                assert not select.select([provider_listener], [], [], 0)[0]
                cancellation = runtime.runs.cancel(run_id)
                assert cancellation.status.cancel_request_satisfied
                detail = runtime.runs.wait_for_terminal(run_id, timeout=20)
                handles = {process.root_fd, process.parent_fd, process.child_fd}
                assert set(select.select(list(handles), [], [], 0)[0]) == handles
                assert not select.select([provider_listener], [], [], 0)[0]
                assert detail.run.status is FleetRunStatus.CANCELLED
                assert detail.outcome is FleetRunOutcome.CANCELLED
                step = detail.game_tape.repositories[0].steps[0]
                assert step.status is GameTapeStatus.CANCELLED
                assert step.settlement.cleanup == "VERIFIED"
                assert step.cancellation is not None
                assert step.cancellation.physical_execution_id == original.physical_execution_id
                assert step.cancellation.supervisor is not None
                assert step.cancellation.supervisor.final_process_group_quiescent is True
                assert not step.native_agent_observations
            durable = Journal(observation_database).run_events(run_id)
            assert durable.run.status is FleetRunStatus.CANCELLED
            terminal_chain = [
                event
                for event in durable.events
                if isinstance(
                    event,
                    (TerminationRequestedEvent, ProcessExitedEvent, DescendantsQuiescentEvent),
                )
                and event.physical_execution_id == original.physical_execution_id
            ]
            assert [type(event) for event in terminal_chain] == [
                TerminationRequestedEvent,
                ProcessExitedEvent,
                DescendantsQuiescentEvent,
            ]
            assert durable.pi_session is None
            assert source.read_text(encoding="utf-8") == "VALUE = 42\n"
            assert {
                entry.name: entry.read_bytes() for entry in configuration.iterdir()
            } == original_config
        finally:
            runtime.shutdown(wait_for_runs=True)
