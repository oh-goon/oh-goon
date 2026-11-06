"""The catalog example through native admission, execution and retained export."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest
import yaml

from oh_goon.core.models.agent_providers import (
    AgentModel,
    AgentProvider,
    AgentProvidersConfig,
)

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="Native POSIX example processes"
)


def test_private_code_catalog_example_exports_its_retained_descriptor(
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "workflow"
    shutil.copytree(ROOT / "examples/private-code-catalog", workflow)
    repository = workflow / "sample-repo"
    subprocess.run(["git", "init", "-q", str(repository)], check=True, timeout=10)
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True, timeout=10)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=Demo Contributor",
            "-c",
            "user.email=demo@example.invalid",
            "commit",
            "-qm",
            "Seed synthetic repository",
        ],
        check=True,
        timeout=10,
    )
    selected = workflow / "run.yml"
    configuration = yaml.safe_load(selected.read_text())
    configuration["target"] = {"repository": str(repository)}
    selected.write_text(yaml.safe_dump(configuration, sort_keys=False))
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("OH_GOON_")
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
            self.send_error(500, "The catalog renderer must not request a model")

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
                models={
                    "ambient": AgentModel(provider="ambient", model="must-not-be-used")
                },
            ).model_dump_json()
        )
        environment["OH_GOON_PROVIDERS_FILE"] = str(provider)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:

            def cli(*args: str) -> dict[str, Any]:
                result = subprocess.run(
                    [str(Path(sys.executable).with_name("oh-goon")), *args, "--json"],
                    env=environment,
                    cwd=tmp_path,
                    text=True,
                    capture_output=True,
                    timeout=45,
                )
                assert result.returncode == 0, (result.stdout, result.stderr)
                decoded = json.loads(result.stdout)
                assert isinstance(decoded, dict)
                return decoded

            cli("workflow", "prepare", str(selected), "--register-target", "--no-input")
            inspected = cli("workflow", "inspect", str(selected))
            assert inspected["status"] == "inputs-valid"
            validated = cli("workflow", "validate", str(selected))
            assert validated["proof"]["provider"] == "not-applicable"
            receipt = cli("run", str(selected))
            assert receipt["status"] == "COMPLETED"
            assert receipt["outcome"] == "SUCCESS"
            run_id = receipt["fleet_run_id"]
            tape = cli("runs", "tape", run_id)
            assert tape["status"] == "PASSED"
            steps = cli("runs", "steps", run_id)["repositories"][0]["steps"]
            assert len(steps) == 1
            step = steps[0]
            assert step["status"] == "PASSED"
            assert step["kind"] == "command"
            assert step["attempts"][0]["returncode"] == 0
            outputs = cli("runs", "outputs", run_id)["repositories"][0]["steps"]
            assert len(outputs) == 1
            artifacts = outputs[0]["artifacts"]
            assert len(artifacts) == 1
            artifact = artifacts[0]
            assert artifact["path"] == "catalog-info.yaml"
            assert artifact["status"] == "PRESENT"

            expected = (
                ROOT / "examples/private-code-catalog/expected/catalog-info.yaml"
            ).read_bytes()
            assert artifact["sha256"] == hashlib.sha256(expected).hexdigest()
            # A later edit to caller metadata cannot rewrite a retained Run artifact.
            (workflow / "generator/catalog-input.json").write_text("{}")
            destination = tmp_path / "exported-catalog-info.yaml"
            exported = cli(
                "runs",
                "export-artifact",
                run_id,
                "--step",
                step["step_id"],
                "--path",
                "catalog-info.yaml",
                "--output",
                str(destination),
            )
            assert exported["status"] == "exported"
            assert exported["cleanup_status"] == "verified"
            assert destination.read_bytes() == expected
            descriptor = yaml.safe_load(destination.read_text())
            assert descriptor["spec"]["owner"] == "group:default/platform-team"
            assert requests == []
            clean = subprocess.run(
                ["git", "-C", str(repository), "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert clean.stdout == ""
        finally:
            server.shutdown()
            worker.join(timeout=5)
            assert not worker.is_alive()
