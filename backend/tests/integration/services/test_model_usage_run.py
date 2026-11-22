"""Actual claimed worker/provider processes retain usage in original Run evidence."""

from __future__ import annotations

import json
import os
import subprocess
from contextlib import closing
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
import yaml

from oh_goon.api import dependencies
from oh_goon.api.app import create_app
from oh_goon.application import ApplicationRuntime
from oh_goon.cli import main
from oh_goon.cli_configuration import prepare_configuration
from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime import run_stateful_command
from oh_goon.cli_runtime_plans import run_plan_command
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.run_events import ProcessStartedEvent
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from tests.factories.agent_review import agent_review_case
from tests.integration.api.client import authenticated_api_client

pytestmark = [pytest.mark.process, pytest.mark.no_resume]


def test_provider_usage_survives_original_run_reopen_api_and_cli(tmp_path, monkeypatch, capsys):
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            assert 0 < length <= 1024 * 1024
            request = json.loads(self.rfile.read(length))
            source = json.loads(request["messages"][1]["content"])
            observed.append(source["identity"]["reviewer"])
            record = dict(
                schema="oh-goon.agent-review/v1",
                **source["identity"],
                verdict="CLEAN",
                summary="Completed selected review",
                reviewed_paths=[member["path"] for member in source["repository"]],
                findings=[],
            )
            response = {
                "id": "same-provider-handle-is-not-an-additive-observation-id",
                "model": "actual/reviewer",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(record)}}],
                "private_debug": "Private provider transcript must never become usage",
            }
            if len(observed) == 1:
                response["usage"] = {
                    "prompt_tokens": 19,
                    "completion_tokens": 0,
                    "total_tokens": 19,
                    "prompt_tokens_details": {"cached_tokens": 0},
                    "private_debug": "Private provider transcript must never become usage",
                }
            data = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    database_path = tmp_path / "database.sqlite"
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        listener = Thread(target=partial(server.serve_forever, poll_interval=0.01))
        listener.start()
        try:
            _, plan_path, repository, options = agent_review_case(tmp_path)
            plan_path.write_text(
                "execution: {purpose: verification}\ngoons:\n"
                "- import: agents/review\n  as: observed\n  pass: [repo, acceptance]\n"
                "  with: {skill: cookbook/single-source-of-truth-review}\n"
                "- import: agents/review\n  as: unreported\n  pass: [repo, acceptance]\n"
                "  with: {skill: cookbook/single-source-of-truth-review}\n"
            )
            providers = tmp_path / "providers.toml"
            providers.write_text(
                f'default_model = "review"\n[providers.test]\n'
                f'api = "openai-chat-completions"\nbase_url = "http://127.0.0.1:{server.server_port}"\n'
                '[models.review]\nprovider = "test"\nmodel = "requested/reviewer"\n'
            )
            subprocess.run(["git", "init", "-q", str(repository)], check=True, timeout=10)
            monkeypatch.setenv("OH_GOON_DB", str(database_path))
            for owner in ("CONFIG", "DATA", "STATE", "CACHE"):
                monkeypatch.setenv(f"XDG_{owner}_HOME", str(tmp_path / owner.lower()))
            monkeypatch.setenv("OH_GOON_PROVIDERS_FILE", str(providers))
            league = tmp_path / "league.yml"
            league.write_text(yaml.safe_dump({"version": 1, "folders": [str(repository.parent)]}))
            monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(league))
            configuration = tmp_path / "run.yml"
            configuration.write_text(
                yaml.safe_dump(
                    {
                        "version": 2,
                        "source": {"kind": "file-plan", "path": "plan.yml"},
                        "target": {"repository": str(repository)},
                        "inputs": {"repo": "local:repo"},
                        "agent": {
                            "model_profile": "review",
                            "acceptance": "agentic-review",
                            "repository_root": str(options["repository_root"]),
                            "skill_root": ".",
                        },
                    }
                )
            )
            assert main(["workflow", "prepare", str(configuration)]) == 0
            capsys.readouterr()
            runtime = ApplicationRuntime.build(standard_local=True)
            try:
                runtime.locations.add(repository.parent)
                parser = build_parser()
                arguments = parser.parse_args(["run", str(configuration), "--json"])
                prepare_configuration(arguments)
                arguments.command, arguments.plan_command = "plan", "run"
                assert run_plan_command(runtime, arguments) == 0
                run_id = json.loads(capsys.readouterr().out)["fleet_run_id"]
                detail = runtime.runs.get(run_id)
                assert detail.outcome.value == "SUCCESS"
                evidence = detail.operations[0].evidence
                assert isinstance(evidence, FleetPlanOperationEvidence)
                first, unreported = evidence.plan.steps
                usage = first.attempts[0].model_usage
                assert usage is not None
                measurement = usage.observations[0]
                assert measurement.source == "openai-chat-completions"
                assert measurement.scope == "request"
                assert measurement.input_tokens == measurement.total_tokens == 19
                assert measurement.output_tokens == measurement.cache_read_tokens == 0
                assert measurement.reasoning_tokens is None
                assert measurement.requested_model == "requested/reviewer"
                assert measurement.reported_model == "actual/reviewer"
                assert measurement.cost is None
                assert "Private provider transcript" not in usage.model_dump_json()
                assert unreported.attempts[0].model_usage is None
                assert first.attempts[0].physical_execution_id
                assert (
                    unreported.attempts[0].physical_execution_id
                    != first.attempts[0].physical_execution_id
                )
                processes = [
                    event
                    for event in detail.lifecycle_events
                    if isinstance(event, ProcessStartedEvent)
                ]
                assert processes and all(event.pid != os.getpid() for event in processes)
                app = create_app(local_access_token="usage-test-local-capability")
                app.dependency_overrides[dependencies.fleet_run_control_plane] = (
                    lambda: runtime.runs
                )
                with closing(authenticated_api_client(app)) as client:
                    response = client.get(f"/api/fleet/runs/{run_id}")
                    assert response.status_code == 200
                    api_step = response.json()["game_tape"]["repositories"][0]["steps"][0]
                    assert api_step["attempts"][0]["model_usage"] == usage.model_dump(mode="json")
                assert (
                    run_stateful_command(
                        runtime, parser.parse_args(["dev", "explain-run", run_id, "--json"])
                    )
                    == 0
                )
                tape = json.loads(capsys.readouterr().out)
                assert tape["repositories"][0]["steps"][0]["attempts"][0][
                    "model_usage"
                ] == usage.model_dump(mode="json")
            finally:
                runtime.shutdown(wait_for_runs=True)
            reopened = Journal(LocalDatabase(database_path)).read_view().required_run_detail(run_id)
            assert reopened.game_tape.repositories[0].steps[0].attempts[0].model_usage == usage
            assert reopened.game_tape.repositories[0].steps[1].attempts[0].model_usage is None
            assert len(observed) == 2
        finally:
            server.shutdown()
            listener.join(timeout=5)
        assert not listener.is_alive()
