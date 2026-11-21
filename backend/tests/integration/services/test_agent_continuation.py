"""Stopped regression -> inspected current bytes -> explicit fresh native Run.

The native protocol fixture proves product lifecycle wiring, not installed-agent
or environment isolation qualification.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from oh_goon.api import dependencies
from oh_goon.api.app import create_app
from oh_goon.application import ApplicationRuntime
from oh_goon.core.models.agent_continuation import AgentContinuationInspection
from oh_goon.core.models.agent_providers import (
    AgentProvidersConfig,
    CodexLoginConnection,
    NativeAgentDefinition,
    NativeAgentProfile,
)
from tests.integration.api.client import authenticated_api_client
from tests.integration.services.test_agent_regression_recovery import exercise_regression_recovery

pytestmark = [pytest.mark.process, pytest.mark.no_resume]


def test_failed_campaign_continues_through_api_and_cli_without_rewriting_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = exercise_regression_recovery(tmp_path, monkeypatch, false_clean=True)
    assert previous["stop_reason"] == "regression_failed"
    repo = tmp_path / "repos/repo"
    regression = (repo / "regression.txt").read_bytes()
    native = tmp_path / "native"
    native.mkdir()
    home = tmp_path / "native-auth"
    home.mkdir()
    os.mkfifo(home / "auth.json")
    program = native / "codex"
    started = native / "started"
    feedback = "Repair only code.txt; keep regression.txt unchanged."
    program.write_text(
        "#!/usr/bin/python3\n"
        "import json,pathlib,sys,tomllib\n"
        "config={}\n"
        "for i,arg in enumerate(sys.argv):\n"
        "    if arg == '-c': config.update(tomllib.loads(sys.argv[i+1]))\n"
        "prompt=json.loads(pathlib.Path(config['model_instructions_file']).read_bytes())\n"
        f"assert prompt['task']=={feedback!r}\n"
        "assert sys.stdin.buffer.read()==prompt['task'].encode()\n"
        "assert 'Hashmarks' in prompt['repository_evidence']\n"
        "assert 'UNTRUSTED_PRIOR_RUN_CONTEXT' in json.dumps(prompt)\n"
        f"with pathlib.Path({str(started)!r}).open('a') as stream: stream.write('started\\n')\n"
        "pathlib.Path('code.txt').write_text('repaired\\n')\n"
        "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'Done'}}))\n"
        "print(json.dumps({'type':'turn.completed'}))\n"
    )
    program.chmod(0o700)
    catalog = native / "providers.json"
    catalog.write_text(
        AgentProvidersConfig(
            default_native_profile="signed-in",
            native_agents={"codex": NativeAgentDefinition(executable=str(program))},
            native_profiles={
                "signed-in": NativeAgentProfile(
                    workspace_roots=(str(repo),),
                    timeout_seconds=30,
                    codex_login=CodexLoginConnection(
                        home=str(home), model="gpt-6-luna", reasoning_effort="low"
                    ),
                )
            },
        ).model_dump_json()
    )
    monkeypatch.setenv("OH_GOON_PROVIDERS_FILE", str(catalog))
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        failed_id = previous["failed_run"]
        old_failure = runtime.runs.get(failed_id).model_dump(
            mode="json",
            exclude={"journal_revision", "campaign_journal"},
            exclude_computed_fields=True,
        )
        old_events = runtime.runs.events(failed_id, limit=100)
        assert not old_events.has_more
        old_campaign = runtime.runs.campaign_journal("regression-recovery").model_dump(
            mode="json", exclude={"journal_revision"}
        )
        app = create_app(local_access_token="continuation-test-token")
        app.dependency_overrides[dependencies.fleet_plan_service] = lambda: runtime.plans
        app.dependency_overrides[dependencies.fleet_run_control_plane] = lambda: runtime.runs
        route = "/api/fleet/runs/campaigns/regression-recovery"
        with closing(authenticated_api_client(app)) as client:
            denied = client.get(
                route + "/continuation", headers={"X-Oh-Goon-Local-Access": "wrong"}
            )
            assert denied.status_code == 401
            response = client.get(route + "/continuation", params={"implementation": "codex"})
            assert response.status_code == 200, response.text
            inspection = AgentContinuationInspection.model_validate(response.json())
            assert inspection.can_continue, inspection.refusal_reasons
            assert inspection.verification_status == "available", inspection.verification_reason
            assert inspection.source.diff.availability == "available"
            assert inspection.source.recorded_candidate_identity
            assert inspection.selection.environment_qualification == "unqualified"
            request = {
                "new_fleet_run_id": "fresh-native-continuation",
                "review": inspection.review.model_dump(mode="json"),
                "feedback": feedback,
                "authorize": True,
            }
            refused = client.post(route + "/continuations", json={**request, "authorize": False})
            assert refused.status_code == 422
            assert not started.exists()
            before = (repo / "code.txt").read_text()
            (repo / "code.txt").write_text("changed-after-inspection\n")
            stale = client.post(route + "/continuations", json=request)
            assert stale.status_code == 409, stale.text
            assert not started.exists()
            (repo / "code.txt").write_text(before)
            with ThreadPoolExecutor(max_workers=2) as callers:
                attempts = [
                    callers.submit(client.post, route + "/continuations", json=request)
                    for _ in range(2)
                ]
                responses = [attempt.result(timeout=45) for attempt in attempts]
            assert all(response.status_code == 200 for response in responses), [
                response.text for response in responses
            ]
            assert {response.json()["acknowledgment"] for response in responses} == {
                "admitted",
                "existing",
            }
            assert all(
                response.json()["fleet_run_id"] == request["new_fleet_run_id"]
                for response in responses
            )
            detail = runtime.runs.wait_for_terminal(request["new_fleet_run_id"])
            assert detail.outcome.value == "SUCCESS", runtime.runs.game_tape_live_output(
                detail.run.fleet_run_id
            ).model_dump_json()
            assert detail.feedback.continuation_state == "verified_success"
            assert len(detail.game_tape.repositories[0].steps) >= 2
            assert all(step.status == "PASSED" for step in detail.game_tape.repositories[0].steps)
            assert (repo / "code.txt").read_text() == "repaired\n"
            assert (repo / "regression.txt").read_bytes() == regression
            # Acknowledgment lookup/retry precedes source recapture: the child changed source.
            duplicate = client.post(route + "/continuations", json=request)
            assert duplicate.status_code == 200, duplicate.text
            assert duplicate.json()["acknowledgment"] == "existing"
            conflict = client.post(
                route + "/continuations", json={**request, "feedback": "Different task"}
            )
            assert conflict.status_code == 409
        inspection_file, feedback_file = native / "inspection.json", native / "feedback.txt"
        inspection_file.write_text(inspection.model_dump_json(by_alias=True))
        feedback_file.write_text(feedback)
        cli = Path(sys.executable).with_name("oh-goon")
        replay = subprocess.run(
            (
                str(cli),
                "runs",
                "continue",
                "regression-recovery",
                "--inspection",
                str(inspection_file),
                "--feedback-file",
                str(feedback_file),
                "--new-run-id",
                request["new_fleet_run_id"],
                "--authorize",
                "--json",
            ),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert replay.returncode == 0, replay.stderr + replay.stdout
        assert json.loads(replay.stdout)["continuation"]["acknowledgment"] == "existing"
        investigate = subprocess.run(
            (
                str(cli),
                "runs",
                "investigate",
                "regression-recovery",
                "--agent",
                "codex",
            ),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert investigate.returncode == 0, investigate.stderr + investigate.stdout
        assert "Current candidate:" in investigate.stdout
        assert started.read_text() == "started\n"
        # Current read-cut revisions advance; original durable history must not.
        assert (
            runtime.runs.get(failed_id).model_dump(
                mode="json",
                exclude={"journal_revision", "campaign_journal"},
                exclude_computed_fields=True,
            )
            == old_failure
        )
        assert runtime.runs.events(failed_id, limit=100).events == old_events.events
        assert runtime.runs.get(failed_id).game_tape.repositories[0].steps[0].status == "FAILED"
        assert (
            runtime.runs.campaign_journal("regression-recovery").model_dump(
                mode="json", exclude={"journal_revision"}
            )
            == old_campaign
        )
    finally:
        runtime.shutdown(wait_for_runs=True)
