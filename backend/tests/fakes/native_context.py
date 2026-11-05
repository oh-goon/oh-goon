"""Controlled native protocol/process traffic through the original Fleet owners.

This extracts the existing ordinary native-context Run fixture. It supplies
explicit task-free requests and protocol frames, never a compaction algorithm
or installed-harness qualification.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, Literal

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentProvidersConfig,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.engine.git import GitService
from oh_goon.integrations.agent_native_execution import (
    NativeAgentCommand,
    native_agent_command,
    native_executable_sha256,
)
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import native_profile_command_runner
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority


@dataclass(frozen=True)
class ControlledNativeContext:
    runtime: ApplicationRuntime
    detail: FleetRunDetail
    selected: ResolvedNativeAgent
    requests: list[dict[str, Any]]
    prompt: dict[str, Any]
    task: str
    skill_text: str
    source: Path


@contextmanager
def controlled_native_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    agent: Literal["pi", "codex", "opencode"],
    verification_value: int,
    role: Literal["report", "review"] = "report",
    review_payload: Literal["typed", "generic"] = "typed",
) -> Iterator[ControlledNativeContext]:
    observed: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            size = int(self.headers["Content-Length"])
            assert 0 < size <= 1024 * 1024
            assert self.headers["Authorization"] == "Bearer host-only-key"
            body = json.loads(self.rfile.read(size))
            assert body["model"] == "fixture/model"
            observed.append(body)
            assert len(observed) <= 3
            payload = json.dumps(
                {"choices": [{"message": {"content": "fixture response"}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            pass

    repo = tmp_path / "teams" / "app"
    repo.mkdir(parents=True)
    source = repo / "code.py"
    source.write_text("VALUE = 41\n" if role == "report" else "VALUE = 42\n")
    (repo / "verify.py").write_text(
        f"from code import VALUE\nassert VALUE == {verification_value}\n"
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    skill = tmp_path / "skills" / "fixture" / "native"
    skill.mkdir(parents=True)
    skill_text = "# Fixture Skill\nRetain the native-skill-canary.\n"
    (skill / "SKILL.md").write_text(skill_text)
    task = (
        "Read code.py, retain native-skill-canary, and change VALUE to 42."
        if role == "report"
        else "Review the complete captured source without changing code.py."
    )
    executable = tmp_path / "native-fixture"
    executable.write_text(
        "#!/usr/bin/python3\n"
        "import json, os, pathlib, sys, tomllib, urllib.request\n"
        f"agent={agent!r}\nrole={role!r}\nreview_payload={review_payload!r}\n"
        "if agent == 'codex':\n"
        "    config=tomllib.loads(pathlib.Path(os.environ['CODEX_HOME'],'config.toml').read_text())\n"
        "    assert config['web_search']=='disabled'\n"
        "assert 'NATIVE_RUN_KEY' not in os.environ\n"
        "assert os.environ['OH_GOON_NATIVE_BROKER_KEY']=='oh-goon-local'\n"
        "prompt=json.load(sys.stdin)\n"
        "assert prompt['working_workspace']['repository_access']==('write' if role=='report' else 'read_only')\n"
        "for index,label in enumerate(('initial reasoning','tool output without task history','summarize retained tool history')):\n"
        "    if index==2 and agent=='pi': print(json.dumps({'type':'compaction_start'}),flush=True)\n"
        "    field='input' if agent=='codex' else 'messages'\n"
        "    body=json.dumps({'model':'fixture/model',field:[{'role':'user','content':label}]}).encode()\n"
        "    route='responses' if agent=='codex' else 'chat/completions'\n"
        "    request=urllib.request.Request('http://127.0.0.1:18743/v1/'+route,data=body,headers={'Content-Type':'application/json'})\n"
        "    with urllib.request.urlopen(request,timeout=10) as response: assert json.load(response)['choices']\n"
        "    if index==2 and agent=='pi': print(json.dumps({'type':'compaction_end'}),flush=True)\n"
        "if role=='report':\n"
        "    pathlib.Path('code.py').write_text('VALUE = 42\\n')\n"
        "    text='Fixture completed'\n"
        "else:\n"
        "    assert pathlib.Path('code.py').read_text()=='VALUE = 42\\n'\n"
        "    try: pathlib.Path('code.py').write_text('forbidden')\n"
        "    except OSError: pass\n"
        "    else: raise AssertionError('read-only source was writable')\n"
        "    record={'schema':'oh-goon.agent-review/v1',**prompt['identity'],'verdict':'CLEAN','summary':'Reviewed captured source','reviewed_paths':{'complete_scope':prompt['identity']['scope_identity']},'findings':[]}\n"
        "    text=json.dumps(record) if review_payload=='typed' else 'CLEAN'\n"
        "if agent=='pi':\n"
        "    events=[{'type':'message_end','message':{'role':'assistant','content':text,'stopReason':'stop'}},{'type':'agent_settled','aborted':False}]\n"
        "elif agent=='codex':\n"
        "    events=[{'type':'item.completed','item':{'type':'agent_message','text':text}},{'type':'turn.completed'}]\n"
        "else:\n"
        "    events=[{'type':'step_start'},{'type':'text','part':{'text':text,'time':{'end':1}}},{'type':'step_finish','part':{'reason':'stop'}}]\n"
        "for event in events: print(json.dumps(event),flush=True)\n"
    )
    executable.chmod(0o700)
    # Preserve the original fixture's public repository/private-state separation.
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "custom.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("NATIVE_RUN_KEY", "host-only-key")
    prompts: list[dict[str, Any]] = []

    def observe_command(*args: Any, **kwargs: Any) -> NativeAgentCommand:
        invocation = native_agent_command(*args, **kwargs)
        assert invocation.input_file is not None
        prompts.append(json.loads(invocation.input_file.read_bytes()))
        return invocation

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_native_steps.native_agent_command", observe_command
    )
    backend = BubblewrapProcessIsolation(shutil.which("bwrap") or "")
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()
        try:
            catalog = AgentProvidersConfig.model_validate(
                {
                    "providers": {
                        "approved": {
                            "api": "openai-responses"
                            if agent == "codex"
                            else "openai-chat-completions",
                            "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                            "api_key_env": "NATIVE_RUN_KEY",
                        }
                    },
                    "models": {"selected": {"provider": "approved", "model": "fixture/model"}},
                }
            )
            selected = ResolvedNativeAgent(
                agent=agent,
                profile_name="managed",
                definition=NativeAgentDefinition(executable=str(executable)),
                profile=NativeAgentProfile(
                    backend="bubblewrap", inference_profile="selected", timeout_seconds=30.0
                ),
                executable_sha256=native_executable_sha256(executable),
                connection=AgentConnection.select(catalog, "selected"),
                backend_configuration_id=backend.configuration_identity(),
            )
            runner = native_profile_command_runner(
                repo, selected, backend, RepositoryWorkspaceAuthority.pin_repository_subject
            )
            runtime = ApplicationRuntime.build(
                standard_local=True,
                standard_process_isolation=backend,
                git_service=GitService(
                    command_runner=runner,
                    observation_runner=partial(runner, read_only_workspace=True),
                ),
            )
            try:
                runtime.locations.add(repo)
                admitted = runtime.plans.run_native_agent(
                    selected=selected,
                    workspace=repo,
                    skill=skill,
                    task=task,
                    role=role,
                    verification=((sys.executable, "verify.py"),),
                )
                detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id, timeout=45)
                assert len(prompts) == 1
                yield ControlledNativeContext(
                    runtime, detail, selected, observed, prompts[0], task, skill_text, source
                )
            finally:
                runtime.shutdown(wait_for_runs=True)
        finally:
            server.shutdown()
            worker.join(timeout=5)
    assert not worker.is_alive()
