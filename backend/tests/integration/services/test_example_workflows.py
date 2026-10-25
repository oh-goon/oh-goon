"""Checked-in examples through the installed CLI, Fleet and durable Game Tape."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
import yaml

from oh_goon.core.models.agent_providers import AgentModel, AgentProvider, AgentProvidersConfig

ROOT = Path(__file__).resolve().parents[4]
pytestmark = pytest.mark.skipif(os.name != "posix", reason="Native POSIX example processes")


@pytest.mark.parametrize("example", ["standard-atomic", "agent-skill-local-search"])
def test_checked_in_example_completes_a_locked_fleet_run_and_durable_tape(
    tmp_path: Path, example: str
) -> None:
    if example == "standard-atomic":
        repository = tmp_path / "repositories" / "atomic"
        shutil.copytree(ROOT / "examples" / example, repository)
        workflow = repository
        approved = repository.parent
    else:
        workflow = tmp_path / "workflow"
        shutil.copytree(ROOT / "examples" / example, workflow)
        repository = workflow / "repos/team/app"
        approved = workflow / "repos"
    configuration = workflow / "run.yml"
    selection = yaml.safe_load(configuration.read_text())
    if example == "agent-skill-local-search":
        selection["agent"]["repository_root"] = str(approved)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("OH_GOON_")
    }
    environment.update(
        OH_GOON_DB=str(tmp_path / "state.sqlite"),
        OH_GOON_LEAGUE_FILE=str(tmp_path / "league.yml"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_DATA_HOME=str(tmp_path / "data"),
    )
    requests: list[str] = []

    class UnexpectedModelRequest(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            requests.append(self.path)
            self.send_error(500, "Source-only examples must not request a model")

        def log_message(self, format: str, *args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), UnexpectedModelRequest) as server:
        provider = tmp_path / "providers.json"
        provider.write_text(
            AgentProvidersConfig(
                default_model="ambient",
                providers={
                    "ambient": AgentProvider(
                        api="ollama", base_url=f"http://127.0.0.1:{server.server_port}"
                    )
                },
                models={"ambient": AgentModel(provider="ambient", model="must-not-be-used")},
            ).model_dump_json()
        )
        environment["OH_GOON_PROVIDERS_FILE"] = str(provider)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:

            def cli(*args: str) -> str:
                result = subprocess.run(
                    [str(Path(sys.executable).with_name("oh-goon")), *args],
                    env=environment,
                    cwd=tmp_path,
                    text=True,
                    capture_output=True,
                    timeout=30,
                )
                assert result.returncode == 0, (result.stdout, result.stderr)
                return result.stdout

            cli("league", "add-folder", str(approved))
            scan = json.loads(cli("league", "scan", "--json"))
            target = next(
                team["repository"]
                for team in scan["teams"]
                if Path(team["repository"]["path"]) == repository
            )
            selection["target"] = {"repository_id": target["repository_id"]}
            configuration.write_text(yaml.safe_dump(selection, sort_keys=False))
            lock = workflow / "goon.lock"
            before = lock.read_bytes() if lock.exists() else None
            inspected = json.loads(cli("config", "validate", str(configuration), "--json"))
            assert inspected["proof"]["provider"] == "not-applicable"
            assert (lock.read_bytes() if lock.exists() else None) == before
            cli("lock", str(configuration))
            result = json.loads(cli("run", str(configuration), "--json"))
            assert result["status"] == "COMPLETED" and result["outcome"] == "SUCCESS"
            tape = json.loads(cli("plan", "tape", result["fleet_run_id"], "--json"))
            assert tape["fleet_run_id"] == result["fleet_run_id"]
            assert tape["status"] == "PASSED"
            assert len(tape["repositories"]) == 1
            steps = tape["repositories"][0]["steps"]
            assert len(steps) == 1 and steps[0]["status"] == "PASSED"
            assert steps[0]["attempts"][0]["returncode"] == 0
            assert requests == []
        finally:
            server.shutdown()
            worker.join(timeout=5)
            assert not worker.is_alive()
