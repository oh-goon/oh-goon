"""Explicit provider-history permission through actual claimed native Run workers.

The local provider deliberately retains history by synthetic account/project/route.
This is a wiring counterfactual, not a guarantee about any live provider or harness.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from textwrap import dedent
from threading import Event, Thread
from typing import Any

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_ai import resolve_native_cli_selection
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import FleetRunEventType, FleetRunOutcome, GameTapeStatus
from oh_goon.engine.atomic_execution_bytes import atomic_execution_bytes_supported
from oh_goon.engine.git import GitService
from oh_goon.integrations.hashmarks import require_loaded_hashmarks_runtime


@pytest.mark.process
@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="native protected Run transport")
@pytest.mark.skipif(
    sys.platform != "linux" or not shutil.which("bwrap"), reason="native Bubblewrap"
)
def test_native_run_history_requires_selected_model_permission_and_provider_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default denial precedes upstream work; opt-in retains provider authorization."""
    workspace = tmp_path / "projects" / "app"
    workspace.mkdir(parents=True)
    assert GitService().run("init", str(workspace)).returncode == 0
    verifier = "from source import VALUE\nassert VALUE == 42\nprint('independent value verified')\n"
    (workspace / "verify.py").write_text(verifier)
    skill = tmp_path / "skills" / "history"
    skill.mkdir(parents=True)
    database = tmp_path / "state" / "app.sqlite"
    for name, value in (
        ("OH_GOON_DB", database),
        ("XDG_CONFIG_HOME", tmp_path / "config"),
        ("XDG_DATA_HOME", tmp_path / "data"),
        ("XDG_STATE_HOME", tmp_path / "state"),
    ):
        monkeypatch.setenv(name, str(value))
    monkeypatch.delenv("OH_GOON_EXECUTORS_FILE", raising=False)
    executable = tmp_path / "native-history-client"
    executable.write_text(
        dedent(
            """\
            #!/usr/bin/python3
            import json,os,pathlib,sys,tomllib,urllib.error,urllib.request
            prompt=json.load(sys.stdin)
            phase=prompt['task'][-1]
            assert phase in ('A','B','C','D')
            assert not any(name.startswith('HUG106_') for name in os.environ)
            assert not any(flag in sys.argv for flag in ('resume','--resume','--session','--continue','--attach'))
            assert '--ephemeral' in sys.argv
            expected_source=f"VALUE = 42\\nPHASE = {phase!r}\\n"
            assert pathlib.Path(prompt['captured_repository'],'source.py').read_text()==expected_source
            assert pathlib.Path('source.py').read_text()==expected_source
            expected_skill=f'# History Skill {phase}\\nUse only current {phase} source and independent verification.\\n'
            assert pathlib.Path(prompt['captured_skill'],'SKILL.md').read_text()==expected_skill
            assert prompt['skill_instructions']==expected_skill
            evidence=json.loads(prompt['repository_evidence'])
            assert evidence['producer']=='Hashmarks'
            assert evidence['capture_identity']==prompt['identity']['candidate_identity']
            assert prompt['working_workspace']['repository_access']=='write'
            context=prompt['context']
            if phase=='A':
                assert context=={}
                reference=None
            else:
                assert context['authority']=='UNTRUSTED_PRIOR_RUN_CONTEXT'
                assert context['permission_authority']=='NONE; fresh source capture and phase admission required'
                if phase=='B':
                    assert context['prior_run_handoff']['allow_provider_history'] is True
                reference=(None if phase=='B' else context['prior_run_handoff']['provider_history_reference'])
            config=tomllib.loads(pathlib.Path(os.environ['CODEX_HOME'],'config.toml').read_text())
            provider=config['model_providers']['oh_goon']
            assert provider['wire_api']=='responses'
            for turn in range(2 if phase=='B' else 1):
                message={'phase':phase,'turn':turn,'identity':prompt['identity'],
                         'task':prompt['task'],'skill_instructions':prompt['skill_instructions'],
                         'repository_evidence':prompt['repository_evidence']}
                body={'model':config['model'],'input':[{'role':'user','content':json.dumps(message)}]}
                if reference is not None:
                    body['previous_response_id']=reference
                request=urllib.request.Request(provider['base_url']+'/responses',data=json.dumps(body).encode(),
                    headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ[provider['env_key']],
                             'OpenAI-Project':'frontend-spoof','OpenAI-Organization':'frontend-spoof'})
                try:
                    with urllib.request.urlopen(request,timeout=45) as response:
                        reply=json.load(response)
                except urllib.error.HTTPError as error:
                    assert phase in ('B','D') and error.code==(400 if phase=='B' else 502)
                    refusal=json.load(error)
                    assert refusal['error']['type']=='inference_boundary'
                    assert isinstance(refusal['error']['message'],str) and refusal['error']['message']
                    assert all(marker not in json.dumps(refusal) for marker in
                               ('SYNTHETIC_FOREIGN_HISTORY_BODY','SYNTHETIC_PROVIDER_CREDENTIAL_BODY'))
                    print('Provider history reference refused',file=sys.stderr,flush=True)
                    print(json.dumps({'type':'turn.failed','error':{'message':'Provider history reference refused'}}),flush=True)
                    sys.exit(17)
                assert phase!='D', 'foreign provider history was accepted'
                historical=reply['history']
                prior_phase='B' if phase=='B' else 'A'
                assert historical['task']==f'Fresh history task {prior_phase}'
                assert historical['skill_instructions']==f'# History Skill {prior_phase}\\nUse only current {prior_phase} source and independent verification.\\n'
                prior_packet=json.loads(historical['repository_evidence'])
                assert prior_packet['producer']=='Hashmarks'
                assert prior_packet['capture_identity']==historical['identity']['candidate_identity']
                if phase in ('A','B'):
                    assert historical=={name:prompt[name] for name in ('task','skill_instructions','repository_evidence','identity')}
                else:
                    assert historical['identity']['candidate_identity']!=prompt['identity']['candidate_identity']
                    assert historical['identity']['skill_identity']!=prompt['identity']['skill_identity']
                    assert historical['repository_evidence']!=prompt['repository_evidence']
                reference=(context['prior_run_handoff']['provider_history_reference'] if phase=='B' else reply['id'])
            print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'Provider history received; independent verification still required'}}),flush=True)
            print(json.dumps({'type':'turn.completed'}),flush=True)
            """
        )
    )
    executable.chmod(0o700)
    config_file = tmp_path / "providers.json"
    reference_a = "opaque-fixture-history-a"
    refusal_canaries = ("SYNTHETIC_FOREIGN_HISTORY_BODY", "SYNTHETIC_PROVIDER_CREDENTIAL_BODY")
    histories: dict[tuple[str, str | None, str | None, str | None, str], dict[str, Any]] = {}
    requests: list[tuple[str, str | None, str | None, str | None, dict[str, Any]]] = []
    arrived = {phase: Event() for phase in ("A", "B", "C", "D")}
    release = {phase: Event() for phase in ("A", "B", "C", "D")}
    producer_identity = require_loaded_hashmarks_runtime()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            size = int(self.headers["Content-Length"])
            if not 0 < size <= 1024 * 1024:
                self.send_error(413)
                return
            body = json.loads(self.rfile.read(size))
            auth = self.headers.get("Authorization")
            project = self.headers.get("OpenAI-Project")
            organization = self.headers.get("OpenAI-Organization")
            requests.append((self.path, auth, project, organization, body))
            message = json.loads(body["input"][-1]["content"])
            phase = message["phase"]
            # Independently check the complete privileged current context on every
            # upstream request, including the explicitly authorized C reference.
            assert body["input"][0]["role"] == "developer"
            privileged = body["input"][0]["content"]
            assert message["task"] == f"Fresh history task {phase}"
            assert message["skill_instructions"] == (
                f"# History Skill {phase}\nUse only current {phase} source and independent verification.\n"
            )
            for field in ("task", "skill_instructions", "repository_evidence"):
                assert message[field] in privileged
            packet = json.loads(message["repository_evidence"])
            assert packet["producer"] == "Hashmarks"
            assert packet["producer_implementation_identity"] == producer_identity
            assert packet["capture_identity"] == message["identity"]["candidate_identity"]
            assert packet["presentation"] and packet["repository_absence"]
            if phase != "A":
                assert "Fresh history task A" not in privileged
            if not arrived[phase].is_set():
                arrived[phase].set()
                if not release[phase].wait(60):
                    self.send_error(504)
                    return
            scope = (self.path, auth, project, organization)
            reference = body.get("previous_response_id")
            if reference is None:
                assert phase in ("A", "B") and message["turn"] == 0
                # Fresh B receives only its current context. Its later attempt
                # to retrieve A must be denied before reaching this provider.
                reference = "opaque-fixture-history-" + phase.lower()
                histories[(*scope, reference)] = {
                    field: message[field]
                    for field in ("task", "skill_instructions", "repository_evidence", "identity")
                }
            history = histories.get((*scope, reference))
            if history is None:
                status = 403
                payload = json.dumps({"error": " ".join(refusal_canaries)}).encode()
            else:
                status = 200
                payload = json.dumps({"id": reference, "history": history}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            pass

    def publish_catalog(phase: str, port: int) -> None:
        scope = "foreign" if phase == "D" else "shared"
        monkeypatch.setenv("HUG106_ACCOUNT", f"synthetic-{scope}-account")
        monkeypatch.setenv("HUG106_PROJECT", f"synthetic-{scope}-project")
        monkeypatch.setenv("HUG106_ORGANIZATION", f"synthetic-{scope}-organization")
        config_file.write_text(
            json.dumps(
                {
                    "default_native_profile": "history",
                    "native_agents": {"codex": {"executable": str(executable)}},
                    "native_profiles": {
                        "history": {
                            "backend": "bubblewrap",
                            "inference_profile": "fixture",
                            "timeout_seconds": 120,
                            "allowed_agents": ["codex"],
                            "workspace_roots": [str(workspace)],
                        }
                    },
                    "providers": {
                        "fixture": {
                            "api": "openai-responses",
                            "base_url": f"http://127.0.0.1:{port}/{scope}",
                            "api_key_env": "HUG106_ACCOUNT",
                            "headers_from_env": {
                                "OpenAI-Project": "HUG106_PROJECT",
                                "OpenAI-Organization": "HUG106_ORGANIZATION",
                            },
                        }
                    },
                    "models": {
                        "fixture": {
                            "provider": "fixture",
                            "model": "fixture",
                            **({"allow_provider_history": True} if phase in ("C", "D") else {}),
                        }
                    },
                }
            )
        )

    recorded: list[FleetRunDetail] = []
    worker_sessions: set[str] = set()
    runtime: ApplicationRuntime | None = None
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server_thread = Thread(target=server.serve_forever)
        server_thread.start()
        try:
            for phase in ("A", "B", "C", "D"):
                (workspace / "source.py").write_text(f"VALUE = 42\nPHASE = {phase!r}\n")
                (skill / "SKILL.md").write_text(
                    f"# History Skill {phase}\nUse only current {phase} source and independent verification.\n"
                )
                publish_catalog(phase, server.server_port)
                selected, isolation, git = resolve_native_cli_selection(
                    agent="codex", profile=None, workspace=workspace, config_file=config_file
                )
                runtime = ApplicationRuntime.build(
                    standard_local=True, standard_process_isolation=isolation, git_service=git
                )
                runtime.locations.add(workspace.parent)
                admitted = runtime.plans.run_native_agent(
                    selected=selected,
                    workspace=workspace,
                    skill=skill,
                    task=f"Fresh history task {phase}",
                    prior_context=(
                        json.dumps(
                            {
                                "provider_history_reference": reference_a,
                                **({"allow_provider_history": True} if phase == "B" else {}),
                            }
                        )
                        if phase != "A"
                        else None
                    ),
                    verification=((sys.executable, "verify.py"),),
                )
                assert arrived[phase].wait(45), runtime.runs.game_tape_live_output(
                    admitted.fleet_run_id
                ).model_dump_json()
                with closing(
                    sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
                ) as connection:
                    custody = connection.execute(
                        "SELECT owner_pid, owner_session_id, custody_phase FROM execution_lease"
                        " WHERE fleet_run_id=?",
                        (admitted.fleet_run_id,),
                    ).fetchone()
                assert custody is not None and custody[2] == "WORKER_OWNED"
                assert custody[0] != os.getpid()
                assert custody[1] not in worker_sessions
                worker_sessions.add(custody[1])
                release[phase].set()
                detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id, timeout=60)
                assert detail.outcome is (
                    FleetRunOutcome.ATTENTION_REQUIRED
                    if phase in ("B", "D")
                    else FleetRunOutcome.SUCCESS
                ), runtime.runs.game_tape_live_output(admitted.fleet_run_id).model_dump_json()
                target = detail.run.target
                assert isinstance(target, FleetPlanTarget)
                admitted_native = target.plan.native_agent
                assert admitted_native is not None and admitted_native.connection is not None
                assert admitted_native.connection.selection.allow_provider_history is (
                    phase in ("C", "D")
                )
                steps = detail.game_tape.repositories[0].steps
                assert len(steps) == 2
                assert steps[0].exit_code == (17 if phase in ("B", "D") else 0)
                assert steps[0].status is (
                    GameTapeStatus.FAILED if phase in ("B", "D") else GameTapeStatus.PASSED
                )
                assert steps[1].status is (
                    GameTapeStatus.SKIPPED if phase in ("B", "D") else GameTapeStatus.PASSED
                )
                assert steps[1].exit_code == (None if phase in ("B", "D") else 0)
                assert steps[1].native_agent_observations == ()
                if phase in ("B", "D"):
                    assert steps[1].attempts == ()
                native = steps[0].native_agent_observations[0]
                assert native.authority == "UNTRUSTED_HARNESS_CLAIM"
                assert native.failed is (phase in ("B", "D"))
                for step in steps:
                    for attempt in step.attempts:
                        assert attempt.standard_isolation is not None
                        assert (
                            attempt.standard_isolation.backend.configuration_id
                            == selected.backend_configuration_id
                        )
                        assert (
                            attempt.standard_isolation.host_and_external_ip_networking == "denied"
                        )
                        assert attempt.supervisor is not None
                        assert attempt.supervisor.final_process_group_quiescent is True
                events = runtime.runs.events(admitted.fleet_run_id)
                assert not events.has_more
                assert (
                    sum(
                        event.event_type is FleetRunEventType.RUN_COMPLETED
                        for event in events.events
                    )
                    == 1
                )
                public = "\n".join(
                    (
                        detail.model_dump_json(),
                        events.model_dump_json(),
                        runtime.runs.game_tape_live_output(admitted.fleet_run_id).model_dump_json(),
                    )
                )
                for private in (
                    *refusal_canaries,
                    "synthetic-shared-account",
                    "synthetic-foreign-account",
                ):
                    assert private not in public
                assert (workspace / "source.py").read_text() == f"VALUE = 42\nPHASE = {phase!r}\n"
                assert (workspace / "verify.py").read_text() == verifier
                recorded.append(detail)
                # Fresh admissions cannot rewrite a predecessor's accepted native
                # result or the independent verifier's original disposition.
                for original in recorded:
                    retained = runtime.runs.get(original.run.fleet_run_id)
                    assert retained.run == original.run
                    assert retained.game_tape.repositories[0].steps == (
                        original.game_tape.repositories[0].steps
                    )
                runtime.shutdown(wait_for_runs=True)
                runtime = None
        finally:
            for event in release.values():
                event.set()
            if runtime is not None:
                runtime.shutdown(wait_for_runs=False)
            server.shutdown()
            server_thread.join(timeout=5)
    assert not server_thread.is_alive()
    assert len(requests) == 4
    identities: list[dict[str, Any]] = []
    for path, auth, project, organization, body in requests:
        message = json.loads(body["input"][-1]["content"])
        phase = message["phase"]
        scope = "foreign" if phase == "D" else "shared"
        assert path == f"/{scope}/responses"
        assert auth == f"Bearer synthetic-{scope}-account"
        assert project == f"synthetic-{scope}-project"
        assert organization == f"synthetic-{scope}-organization"
        assert body["model"] == "fixture"
        assert body.get("previous_response_id") == (None if phase in ("A", "B") else reference_a)
        if message["turn"] == 0:
            identities.append(message["identity"])
    assert len(identities) == 4
    for field in ("candidate_identity", "skill_identity"):
        assert len({identity[field] for identity in identities}) == 4
    # The same complete path scope was recaptured; changed bytes and Skill text
    # create fresh candidates without pretending the review membership changed.
    assert len({identity["scope_identity"] for identity in identities}) == 1
    reloaded = ApplicationRuntime.build(startup_purpose="observation")
    try:
        history = reloaded.runs.history(limit=5)
        assert {item.run.fleet_run_id for item in history.items} == {
            original.run.fleet_run_id for original in recorded
        }
        assert len(history.items) == 4 and history.next_before_revision is None
        for original in recorded:
            retained = reloaded.runs.get(original.run.fleet_run_id)
            assert retained.run == original.run
            assert (
                retained.game_tape.repositories[0].steps == original.game_tape.repositories[0].steps
            )
    finally:
        reloaded.shutdown(wait_for_runs=True)
