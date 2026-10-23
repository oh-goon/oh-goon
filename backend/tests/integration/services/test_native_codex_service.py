"""Opt-in model-free permission qualification against an owned native service.

This proves observed command boundaries, not original Run admission, complete
harness coverage, live authentication or configuration-generation identity.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep

import pytest

from oh_goon.core.models.agent_providers import AgentProviderError
from oh_goon.engine.command import CommandCancelledError, CommandResult, run_command
from oh_goon.integrations.agent_codex_service import CodexServiceClient

_PROBE = r"""import json, pathlib, subprocess, sys
repo, scratch, stores, repair = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]), sys.argv[4] == 'repair'
source = repo / 'src' / 'source.py'
def denied(action):
    try:
        action()
    except OSError:
        return True
    return False
facts = {'source_read': source.read_text().startswith('VALUE = ')}
(scratch / 'owned.txt').write_text('scratch')
facts['scratch_write'] = (scratch / 'owned.txt').read_text() == 'scratch'
if repair:
    source.write_text('VALUE = 42\n')
    source.rename(source.with_name('renamed.py'))
    source.with_name('renamed.py').rename(source)
    removable = source.with_name('remove.py')
    removable.write_text('temporary')
    removable.unlink()
    facts['scoped_source_write_rename_delete'] = source.read_text() == 'VALUE = 42\n'
else:
    facts['source_write_denied'] = denied(lambda: source.write_text('unauthorized'))
    facts['source_rename_denied'] = denied(lambda: source.rename(source.with_name('moved.py')))
    facts['source_delete_denied'] = denied(source.unlink)
facts['git_write_denied'] = denied(lambda: (repo / '.git' / 'config').write_text('unauthorized'))
facts['outside_repair_write_denied'] = denied(lambda: (repo / 'outside.py').write_text('unauthorized'))
for name in ('codex-auth.json', 'codex-config.toml', 'opencode-auth.json', 'opencode-config.json'):
    private_file = stores / name
    facts[name + '_read_denied'] = denied(private_file.read_bytes)
    facts[name + '_write_denied'] = denied(lambda: private_file.write_text('unauthorized'))
    facts[name + '_rename_denied'] = denied(lambda: private_file.rename(private_file.with_suffix('.moved')))
    facts[name + '_delete_denied'] = denied(private_file.unlink)
facts['private_alias_read_denied'] = denied(lambda: (repo / 'private-alias').read_bytes())
facts['private_alias_write_denied'] = denied(lambda: (repo / 'private-alias').write_text('unauthorized'))
child = subprocess.run([sys.executable, '-I', '-c', 'import pathlib, sys\ntry: pathlib.Path(sys.argv[1]).read_bytes()\nexcept OSError: sys.exit(0)\nelse: sys.exit(1)', str(stores / 'codex-auth.json')], capture_output=True, timeout=3)
facts['subprocess_private_read_denied'] = child.returncode == 0
print(json.dumps(facts, sort_keys=True))
"""


@pytest.mark.certification
def test_installed_codex_service_keeps_native_stores_outside_phase_commands(tmp_path: Path) -> None:
    executable = os.environ.get("OH_GOON_NATIVE_CODEX")
    runtime_root = os.environ.get("OH_GOON_NATIVE_CODEX_RUNTIME_ROOT")
    if not executable or not runtime_root:
        pytest.skip("Select the installed Codex executable and complete public runtime explicitly")
    if platform.system() != "Linux":
        pytest.skip("This installed native permission qualification targets Linux")
    program, runtime = Path(executable), Path(runtime_root)
    assert program.is_absolute() and runtime.is_absolute() and program.is_relative_to(runtime)
    root = tmp_path / "native-service"
    root.mkdir(mode=0o700)
    workspace, scratch, stores = root / "workspace", root / "scratch", root / "fake-private-stores"
    home, control = root / "home", root / "control"
    for directory in (workspace, scratch, stores, home, control):
        directory.mkdir(mode=0o700)
    (workspace / "src").mkdir()
    (workspace / ".git").mkdir()
    source = workspace / "src" / "source.py"
    source.write_text("VALUE = 41\n")
    git = workspace / ".git" / "config"
    git.write_text("SYNTHETIC_GIT_METADATA")
    outside = workspace / "outside.py"
    outside.write_text("OUTSIDE_REPAIR_SCOPE")
    for name in (
        "codex-auth.json",
        "codex-config.toml",
        "opencode-auth.json",
        "opencode-config.json",
    ):
        (stores / name).write_text("SYNTHETIC_PRIVATE_CANARY")
    (workspace / "private-alias").symlink_to(stores / "codex-auth.json")
    advertised = control / "advertised.sock"
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "CODEX_HOME": str(home / "codex-state"),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "TMPDIR": str(scratch),
    }
    for key in ("CODEX_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        Path(environment[key]).mkdir(mode=0o700)
    assert not runtime.is_relative_to(home) and not home.is_relative_to(runtime)
    filesystem = {
        ":root": "deny",
        ":minimal": "read",
        str(runtime): "read",
        str(workspace): "read",
        str(scratch): "write",
        str(stores): "deny",
        str(home): "deny",
        str(control): "deny",
    }
    settings = [
        'default_permissions="phase-review"',
        'approval_policy="never"',
        'shell_environment_policy.inherit="none"',
        'shell_environment_policy.set={PATH="/usr/local/bin:/usr/bin:/bin",TMPDIR='
        + json.dumps(str(scratch))
        + "}",
        "features.use_legacy_landlock=false",
        "features.hooks=false",
        "features.plugin_hooks=false",
        "features.plugins=false",
        "features.apps=false",
        "features.multi_agent_v2=false",
        "features.skill_mcp_dependency_install=false",
        'web_search="disabled"',
    ]
    for profile, writes in (
        ("phase-review", {}),
        ("phase-repair", {str(workspace / "src"): "write"}),
    ):
        rules = ",".join(
            json.dumps(path) + "=" + json.dumps(access)
            for path, access in (filesystem | writes).items()
        )
        settings.append(
            f"permissions.{profile}={{filesystem={{{rules}}},network={{enabled=false}}}}"
        )
    arguments = tuple(part for setting in settings for part in ("-c", setting))
    cancelled = Event()
    outcomes: list[CommandResult | BaseException] = []

    def serve() -> None:
        try:
            outcomes.append(
                run_command(
                    executable,
                    *arguments,
                    "app-server",
                    "--listen",
                    "unix://" + str(advertised),
                    cwd=workspace,
                    env=environment,
                    timeout=90,
                    cancel_requested=cancelled.is_set,
                    max_capture_bytes_per_stream=65536,
                    require_process_group_quiescence=True,
                )
            )
        except BaseException as error:
            outcomes.append(error)

    server = Thread(target=serve, name="owned-native-codex-service")
    server.start()
    service_observed = False
    try:
        deadline = monotonic() + 10
        while not advertised.exists() and server.is_alive() and monotonic() < deadline:
            sleep(0.01)
        assert advertised.exists(), {
            "service_alive": server.is_alive(),
            "outcomes": [type(item).__name__ for item in outcomes],
        }

        service_observed = True

        async def inspect() -> None:
            async with CodexServiceClient(
                advertised, timeout_seconds=10, experimental_api=True
            ) as client:
                assert client.metadata["service_version"] == "0.163.0"
                assert client.metadata["execution_admitted"] is False
                available = await client.list_permission_profiles(cwd=workspace)
                assert {"phase-review", "phase-repair"} <= {
                    item["id"] for item in available["data"]
                }
                for profile, phase in (
                    ("phase-review", "review"),
                    ("phase-repair", "repair"),
                    ("phase-review", "rereview"),
                ):
                    result = await client.command_exec(
                        (
                            "/usr/bin/python3",
                            "-I",
                            "-c",
                            _PROBE,
                            str(workspace),
                            str(scratch),
                            str(stores),
                            phase,
                        ),
                        cwd=workspace,
                        permissions=profile,
                        timeout_seconds=8,
                    )
                    (root / (phase + "-command-result.json")).write_text(json.dumps(result))
                    assert result["exitCode"] == 0, {
                        "phase": phase,
                        "exit_code": result["exitCode"],
                    }
                    facts = json.loads(result["stdout"])
                    assert facts and all(value is True for value in facts.values()), {
                        "phase": phase,
                        "facts": facts,
                    }
                assert client.metadata["private_store_exclusion"] == "unverified"
                created = await client.start_thread(
                    cwd=workspace,
                    permissions="phase-review",
                    model="gpt-6.1-sol",
                    developer_instructions="Frozen synthetic phase instructions; no model turn is requested.",
                    ephemeral=False,
                )
                thread_id = created["thread"]["id"]
                assert created["activePermissionProfile"]["id"] == "phase-review"
                assert created["thread"]["turns"] == []
            assert server.is_alive(), "Closing the client must not stop the operator-owned service"
            async with CodexServiceClient(
                advertised, timeout_seconds=10, experimental_api=True
            ) as resumed_client:
                # Native 0.163 materializes persistent history at the first turn.
                # An untouched thread can start, but cannot be resumed after detach.
                with pytest.raises(AgentProviderError, match="refused the request"):
                    await resumed_client.resume_thread(thread_id, permissions="phase-review")
                assert resumed_client.metadata["execution_admitted"] is False
                (root / "thread-continuation-receipt.json").write_text(
                    json.dumps(
                        {
                            "empty_thread_resume": "native_refused_before_first_turn",
                            "persisted_continuation": "unverified",
                            "selected_profile_id": "phase-review",
                            "model_turns_requested": 0,
                            "effective_grants_and_config_generation": "unverified",
                        }
                    )
                )

        asyncio.run(inspect())
        assert source.read_text() == "VALUE = 42\n"
        assert git.read_text() == "SYNTHETIC_GIT_METADATA"
        assert outside.read_text() == "OUTSIDE_REPAIR_SCOPE"
        for private_file in stores.iterdir():
            assert private_file.read_text() == "SYNTHETIC_PRIVATE_CANARY"
        assert {private_file.name for private_file in stores.iterdir()} == {
            "codex-auth.json",
            "codex-config.toml",
            "opencode-auth.json",
            "opencode-config.json",
        }
    finally:
        cancelled.set()
        server.join(timeout=12)
        assert not server.is_alive(), "Native service cleanup did not close its process owner"
        assert len(outcomes) == 1
        outcome = outcomes[0]
        if service_observed:
            assert isinstance(outcome, CommandCancelledError), {"outcome": type(outcome).__name__}
            assert outcome.supervisor.cleanup_reason == "cancel"
            assert outcome.supervisor.final_process_group_quiescent is True
            (root / "service-cleanup-receipt.json").write_text(
                json.dumps(
                    {
                        "outcome": type(outcome).__name__,
                        "cleanup_reason": outcome.supervisor.cleanup_reason,
                        "final_process_group_quiescent": outcome.supervisor.final_process_group_quiescent,
                    }
                )
            )
        elif isinstance(outcome, CommandResult):
            (root / "startup-error.log").write_text(outcome.stderr[-4096:])
            assert outcome.supervisor.final_process_group_quiescent is True
