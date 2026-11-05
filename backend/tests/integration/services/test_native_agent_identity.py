"""Frozen native selections across fresh claimed Run workers, using controlled traffic."""

from __future__ import annotations

import json
import os
import shutil
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from typing import Any

import pytest

from oh_goon._application_foundation import build_runtime_foundation
from oh_goon._application_services import build_product_services
from oh_goon.application import ApplicationRuntime
from oh_goon.cli_ai import resolve_native_cli_selection
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedAgentStep
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import FleetRunEventType, FleetRunOutcome
from oh_goon.engine.atomic_execution_bytes import atomic_execution_bytes_supported
from oh_goon.engine.git import GitService


@pytest.mark.process
@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="native protected Run transport")
@pytest.mark.skipif(
    sys.platform != "linux" or not shutil.which("bwrap"), reason="native Bubblewrap"
)
def test_live_native_run_keeps_its_selection_when_the_catalog_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A held provider turn spans a catalog update; B requires its own fresh admission."""
    workspace = tmp_path / "projects" / "app"
    workspace.mkdir(parents=True)
    assert GitService().run("init", str(workspace)).returncode == 0
    (workspace / "source.py").write_text("VALUE = 42\n")
    (workspace / "verify.py").write_text("from source import VALUE\nassert VALUE == 42\n")
    for name, value in (
        ("OH_GOON_DB", tmp_path / "state" / "app.sqlite"),
        ("XDG_CONFIG_HOME", tmp_path / "config"),
        ("XDG_DATA_HOME", tmp_path / "data"),
        ("XDG_STATE_HOME", tmp_path / "state"),
    ):
        monkeypatch.setenv(name, str(value))
    monkeypatch.setenv("HUG28_KEY_A", "synthetic-key-a")
    monkeypatch.setenv("HUG28_KEY_B", "synthetic-key-b")
    monkeypatch.delenv("OH_GOON_EXECUTORS_FILE", raising=False)
    programs: dict[str, Path] = {}
    for label in ("a", "b"):
        executable = tmp_path / ("native-" + label)
        executable.write_text(
            "#!/usr/bin/python3\n"
            "import json,os,pathlib,sys,urllib.request\n"
            f"label={label!r}\n"
            "assert 'HUG28_KEY_A' not in os.environ and 'HUG28_KEY_B' not in os.environ\n"
            "prompt=json.load(sys.stdin)\n"
            "config=json.loads(pathlib.Path(os.environ['PI_CODING_AGENT_DIR'],'models.json').read_text())\n"
            "provider=config['providers']['oh-goon']\n"
            "for phase in ('initial turn','tool follow-up without prior task'):\n"
            "    text=json.dumps({'executed_fixture':label,'phase':phase,'task':prompt['task'],'identity':prompt['identity']})\n"
            "    body=json.dumps({'model':provider['models'][0]['id'],'messages':[{'role':'user','content':text}]}).encode()\n"
            "    request=urllib.request.Request(provider['baseUrl']+'/chat/completions',data=body,headers={'Content-Type':'application/json'})\n"
            "    with urllib.request.urlopen(request,timeout=60) as response: assert json.load(response)['choices']\n"
            "print('native diagnostic '+label,file=sys.stderr,flush=True)\n"
            "print(json.dumps({'type':'message_end','message':{'role':'assistant','content':'completed '+label,'stopReason':'stop'}}),flush=True)\n"
            "print(json.dumps({'type':'agent_settled','aborted':False}),flush=True)\n"
        )
        executable.chmod(0o700)
        programs[label] = executable
    config_file = tmp_path / "providers.json"
    arrived, release = Event(), Event()
    requests: list[tuple[str, str | None, dict[str, Any]]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            size = int(self.headers["Content-Length"])
            if not 0 < size <= 1024 * 1024:
                self.send_error(413)
                return
            body = json.loads(self.rfile.read(size))
            requests.append((self.path, self.headers.get("Authorization"), body))
            if self.path == "/a/chat/completions" and not arrived.is_set():
                arrived.set()
                if not release.wait(60):
                    self.send_error(504)
                    return
            payload = b'{"choices":[{"message":{"content":"controlled response"}}]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            pass

    def publish_catalog(label: str, port: int) -> None:
        # B entirely replaces A's provider/model/default, rather than adding aliases.
        config_file.write_text(
            json.dumps(
                {
                    "default_native_profile": "profile-" + label,
                    "native_agents": {"pi": {"executable": str(programs[label])}},
                    "native_profiles": {
                        "profile-" + label: {
                            "backend": "bubblewrap",
                            "inference_profile": "model-" + label,
                            "timeout_seconds": 120,
                            "allowed_agents": ["pi"],
                            "workspace_roots": [str(workspace)],
                        }
                    },
                    "providers": {
                        "provider-" + label: {
                            "api": "openai-chat-completions",
                            "base_url": f"http://127.0.0.1:{port}/{label}",
                            "api_key_env": "HUG28_KEY_" + label.upper(),
                        }
                    },
                    "models": {"model-" + label: {"provider": "provider-" + label, "model": label}},
                }
            )
        )

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server_thread = Thread(target=server.serve_forever)
        server_thread.start()
        runtimes: list[ApplicationRuntime] = []
        try:
            publish_catalog("a", server.server_port)
            selected_a, isolation_a, git_a = resolve_native_cli_selection(
                agent="pi", profile=None, workspace=workspace, config_file=config_file
            )
            foundation_a = build_runtime_foundation(
                standard_local=True, standard_process_isolation=isolation_a, git_service=git_a
            )
            runtime_a = build_product_services(foundation_a)
            runtimes.append(runtime_a)
            runtime_a.locations.add(workspace.parent)
            admitted_a = runtime_a.plans.run_native_agent(
                selected=selected_a,
                workspace=workspace,
                skill=None,
                task="Original task A",
                verification=((sys.executable, "verify.py"),),
            )
            assert arrived.wait(45), "original worker did not reach its controlled provider"
            with foundation_a.database.transaction() as connection:
                custody = connection.execute(
                    "SELECT owner_pid, owner_session_id, custody_phase FROM execution_lease"
                    " WHERE fleet_run_id=?",
                    (admitted_a.fleet_run_id,),
                ).fetchone()
            assert custody is not None and custody[2] == "WORKER_OWNED"
            assert custody[0] != os.getpid()
            assert custody[1] != foundation_a.database.session_id
            publish_catalog("b", server.server_port)
            monkeypatch.setenv("HUG28_KEY_A", "revoked-ambient-a")
            selected_b, isolation_b, git_b = resolve_native_cli_selection(
                agent="pi", profile=None, workspace=workspace, config_file=config_file
            )
            assert selected_a.profile_name == "profile-a"
            assert selected_b.profile_name == "profile-b"
            release.set()
            detail_a = runtime_a.runs.wait_for_terminal(admitted_a.fleet_run_id, timeout=60)
            assert detail_a.outcome is FleetRunOutcome.SUCCESS
            # A runtime's scoped Git owner is frozen too. It cannot carry B's
            # separately resolved intent under A's original physical policy.
            revision = foundation_a.database.durable_revision()
            with pytest.raises(FleetPlanInvalid, match="Native Git .*resolved"):
                runtime_a.plans.run_native_agent(
                    selected=selected_b,
                    workspace=workspace,
                    skill=None,
                    task="Must not admit B through A's owner",
                )
            assert foundation_a.database.durable_revision() == revision
            assert len(requests) == 2
            runtime_a.shutdown(wait_for_runs=True)
            runtimes.remove(runtime_a)

            # Phase one deliberately retains one active Run. Start B only after A settles.
            runtime_b = ApplicationRuntime.build(
                standard_local=True, standard_process_isolation=isolation_b, git_service=git_b
            )
            runtimes.append(runtime_b)
            admitted_b = runtime_b.plans.run_native_agent(
                selected=selected_b,
                workspace=workspace,
                skill=None,
                task="New task B",
                verification=((sys.executable, "verify.py"),),
            )
            detail_b = runtime_b.runs.wait_for_terminal(admitted_b.fleet_run_id, timeout=60)
            assert detail_b.outcome is FleetRunOutcome.SUCCESS
            assert admitted_a.fleet_run_id != admitted_b.fleet_run_id
            for label, selected, detail in zip(
                ("a", "b"),
                (selected_a, selected_b),
                (detail_a, detail_b),
                strict=True,
            ):
                task = "Original task A" if label == "a" else "New task B"
                target = detail.run.target
                assert isinstance(target, FleetPlanTarget)
                assert target.plan.native_agent == selected
                agent_step = target.plan.steps[0]
                assert isinstance(agent_step, ResolvedAgentStep)
                assert agent_step.workload.harness == "native"
                assert agent_step.workload.native_agent == selected
                steps = detail.game_tape.repositories[0].steps
                assert len(steps) == 2 and all(step.exit_code == 0 for step in steps)
                for step in steps:
                    attempt = step.attempts[0]
                    assert attempt.standard_isolation is not None
                    assert (
                        attempt.standard_isolation.backend.configuration_id
                        == selected.backend_configuration_id
                    )
                    assert attempt.standard_isolation.host_and_external_ip_networking == "denied"
                    assert attempt.supervisor is not None
                    assert attempt.supervisor.final_process_group_quiescent is True
                assert len(steps[0].native_agent_observations) == 1
                assert steps[0].native_agent_observations[0].settled is True
                assert steps[1].native_agent_observations == ()
                matched = [
                    request for request in requests if request[0] == f"/{label}/chat/completions"
                ]
                assert len(matched) == 2
                identities = []
                for _, auth, body in matched:
                    assert auth == "Bearer synthetic-key-" + label
                    assert body["model"] == label
                    privileged = "\n".join(
                        message["content"]
                        for message in body["messages"]
                        if message["role"] in {"system", "developer"}
                    )
                    assert task in privileged
                    message = json.loads(body["messages"][-1]["content"])
                    assert message["executed_fixture"] == label
                    assert message["task"] == task
                    identities.append(message["identity"])
                assert identities[0] == identities[1]
            assert len(requests) == 4
            retained_a = runtime_b.runs.get(admitted_a.fleet_run_id)
            assert retained_a.run == detail_a.run
            assert (
                retained_a.game_tape.repositories[0].steps
                == detail_a.game_tape.repositories[0].steps
            )
            for run_id in (admitted_a.fleet_run_id, admitted_b.fleet_run_id):
                events = runtime_b.runs.events(run_id).events
                assert (
                    sum(event.event_type is FleetRunEventType.RUN_COMPLETED for event in events)
                    == 1
                )
        finally:
            release.set()
            for runtime in reversed(runtimes):
                runtime.shutdown(wait_for_runs=False)
            server.shutdown()
            server_thread.join(timeout=5)
    assert not server_thread.is_alive()
