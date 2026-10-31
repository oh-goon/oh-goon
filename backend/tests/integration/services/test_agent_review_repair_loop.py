"""Native admitted Steps, local HTTP model, durable phase Runs and fresh re-review."""

import json
import os
import shutil
import subprocess
import sys
from contextlib import closing
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

import pytest
import yaml

from oh_goon.api import dependencies
from oh_goon.api.app import create_app
from oh_goon.application import ApplicationRuntime
from oh_goon.cli import main
from oh_goon.cli_configuration import prepare_configuration
from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime_plans import run_plan_command
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentProvidersConfig,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.agent_repair import AgentRepairBudget
from oh_goon.core.models.agent_review import MAX_REVIEW_BYTES, AgentReviewRecord
from oh_goon.core.models.fleet_observation import AgentReviewObservation
from oh_goon.core.models.fleet_plan_evidence import CompletedPlanStepExecution
from oh_goon.core.models.git_sources import LockedExecutionSource
from oh_goon.core.models.goon_plan_source import SharedPlanSource
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import repository_id
from oh_goon.engine.git import GitService
from oh_goon.integrations.agent_native_execution import native_executable_sha256
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import native_profile_command_runner
from oh_goon.integrations.git_source_preparation import prepare_git_source
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from oh_goon.services.execution_lock_persistence import write_execution_lock
from tests.factories.agent_review import agent_review_case
from tests.factories.python_goon_projects import write_isolated_build_project
from tests.integration.api.client import authenticated_api_client

pytestmark = pytest.mark.daily


@pytest.fixture
def installed_cli(monkeypatch):
    cli = Path(sys.executable).with_name("oh-goon")
    assert cli.is_file(), "native locked environment must provide the apply CLI"
    monkeypatch.setenv("PATH", str(cli.parent) + os.pathsep + os.environ.get("PATH", ""))


@pytest.mark.parametrize("ingress", ["cli", "shared_http"])
def test_single_review_uses_normal_lock_and_run_without_loop_limits(
    tmp_path, monkeypatch, capsys, ingress
):
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            source = json.loads(request["messages"][1]["content"])
            observed.append(source)
            record = dict(
                schema="oh-goon.agent-review/v1",
                **source["identity"],
                verdict="CLEAN",
                summary="Completed selected review",
                reviewed_paths=[m["path"] for m in source["repository"]],
                findings=[],
            )
            payload = json.dumps(
                {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(record)}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=partial(server.serve_forever, poll_interval=0.01))
        worker.start()
        try:
            _, path, repo, options = agent_review_case(tmp_path)
            path.write_text(
                "execution: {purpose: verification}\ngoons:\n- import: agents/review\n"
                "  with: {skill: cookbook/single-source-of-truth-review}\n"
            )
            providers = tmp_path / "providers.toml"
            providers.write_text(f"""default_model = "review"
[providers.test]
api = "openai-chat-completions"
base_url = "http://127.0.0.1:{server.server_port}"
[models.review]
provider = "test"
model = "synthetic/reviewer"
""")
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "database.sqlite"))
            monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
            monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
            monkeypatch.setenv("OH_GOON_PROVIDERS_FILE", str(providers))
            league = tmp_path / "league.yml"
            league.write_text(yaml.safe_dump({"version": 1, "folders": [str(repo.parent)]}))
            monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(league))
            configuration = tmp_path / "run.yml"
            configuration.write_text(
                yaml.safe_dump(
                    {
                        "version": 1,
                        "source": {"kind": "file-plan", "path": "plan.yml"},
                        "target": {"repository_id": repository_id(repo)},
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
            parser = build_parser()
            assert main(["lock", str(configuration)]) == 0
            capsys.readouterr()
            runtime = ApplicationRuntime.build(standard_local=True)
            try:
                runtime.locations.add(repo.parent)
                if ingress == "cli":
                    args = parser.parse_args(["run", str(configuration), "--json"])
                    prepare_configuration(args)
                    args.command, args.plan_command = "plan", "run"
                    assert run_plan_command(runtime, args) == 0
                    receipt = json.loads(capsys.readouterr().out)
                else:
                    document = runtime.shared_plan.save("single-review", path.read_text())
                    app = create_app(local_access_token="shared-agent-test-token")
                    app.dependency_overrides[dependencies.shared_plan_service] = (
                        lambda: runtime.shared_plan
                    )
                    with closing(authenticated_api_client(app)) as client:
                        selected = dict(
                            plan_key=document.plan_key,
                            expected_revision=document.sha256,
                            repository_ids=[repository_id(repo)],
                            inputs={"repo": "local:repo"},
                            agent=dict(
                                model_profile="review",
                                acceptance="agentic-review",
                                repository_root=str(options["repository_root"]),
                                skill_root=str(tmp_path),
                            ),
                        )
                        preview = client.post("/api/league/shared-plan-previews", json=selected)
                        assert preview.status_code == 200, preview.text
                        reviewed = preview.json()
                        request = {
                            key: value
                            for key, value in selected.items()
                            if key != "expected_revision"
                        }
                        request.update(
                            expected_plan_revision=document.sha256,
                            expected_plan_id=reviewed["plan_id"],
                            expected_execution_digest=reviewed["execution_digest"],
                            repository_instance_ids={
                                row["repository"]["repository_id"]: row["repository_instance_id"]
                                for row in reviewed["teams"]
                            },
                            fleet_run_id="shared-single-review",
                        )
                        response = client.post("/api/league/shared-plan-runs", json=request)
                        assert response.status_code == 202, response.text
                        receipt = response.json()
                    runtime.runs.wait_for_terminal(receipt["fleet_run_id"])
                detail = runtime.runs.get(receipt["fleet_run_id"])
                assert detail.outcome.value == "SUCCESS"
                assert detail.run.target.agent_repair is None
                record = detail.operations[0].evidence.plan.steps[0].observations[0].record
                assert record.verdict == "CLEAN"
                assert record.reviewer == observed[0]["identity"]["reviewer"]
                assert len(observed) == 1
            finally:
                runtime.shutdown(wait_for_runs=True)
        finally:
            server.shutdown()
            worker.join(timeout=5)


@pytest.mark.parametrize("template_key", ["agents/agentic-review-repair-loop"])
@pytest.mark.parametrize(
    "scenario,passes,expected,expected_phases,calls",
    [
        (
            "repair",
            2,
            "converged",
            ["review", "adjudicate", "repair", "regression", "review", "verification"],
            10,
        ),
        (
            "shared_http",
            2,
            "converged",
            ["review", "adjudicate", "repair", "regression", "review", "verification"],
            10,
        ),
        (
            "git_plan",
            2,
            "converged",
            ["review", "adjudicate", "repair", "regression", "review", "verification"],
            10,
        ),
        (
            "accepted_nonzero",
            2,
            "converged",
            ["review", "adjudicate", "repair", "regression", "review", "verification"],
            10,
        ),
        (
            "python_phases",
            2,
            "converged",
            ["review", "adjudicate", "repair", "regression", "review", "verification"],
            10,
        ),
        (
            "large_scope",
            2,
            "converged",
            ["review", "adjudicate", "repair", "regression", "review", "verification"],
            10,
        ),
        (
            "remote",
            2,
            "converged",
            ["review", "adjudicate", "repair", "regression", "review", "verification"],
            10,
        ),
        ("budget", 1, "attempts_exhausted", ["review"], 4),
        ("credentials", 2, "converged", ["review", "verification"], 4),
        ("verification", 2, "verification_failed", ["review", "verification"], 4),
        ("leave_alone", 2, "review_incomplete", ["review"], 4),
        ("insufficient", 2, "review_incomplete", ["review"], 4),
        ("mismatched", 2, "review_incomplete", ["review"], 2),
        ("missing_coverage", 2, "review_incomplete", ["review"], 2),
        ("bad_adjudication", 2, "adjudication_incomplete", ["review", "adjudicate"], 5),
        ("missing_regression", 2, "apply_failed", ["review", "adjudicate", "repair"], 6),
        ("scope", 2, "apply_failed", ["review", "adjudicate", "repair"], 6),
        ("regression", 2, "regression_failed", ["review", "adjudicate", "repair", "regression"], 6),
        (
            "arbitrary",
            2,
            "converged",
            ["review", "adjudicate", "repair", "regression", "review", "verification"],
            10,
        ),
        ("stale_candidate", 2, "source_changed", ["review"], 4),
        ("configuration", 2, "source_changed", ["review"], 4),
        (
            "no_progress",
            2,
            "no_progress",
            ["review", "adjudicate", "repair", "regression", "review"],
            10,
        ),
        ("cancel", 2, "cancelled", ["review"], 1),
        ("cancel_repair", 2, "cancelled", ["review", "adjudicate", "repair"], 6),
        ("timeout", 2, "timed_out", ["review"], 1),
    ],
)
def test_native_loop_reserves_review_and_retains_each_phase(
    tmp_path,
    monkeypatch,
    installed_cli,
    template_key,
    scenario,
    passes,
    expected,
    expected_phases,
    calls,
):
    observed = []
    observed_scopes = {}
    large_scope_paths: set[str] = set()
    entered = Event()
    release = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if scenario == "credentials":
                assert self.headers["Authorization"] == "Bearer fixture-secret-not-a-source"
            system = request["messages"][0]["content"]
            source = json.loads(request["messages"][1]["content"])
            identity = source["identity"]
            observed.append(identity)
            if scenario == "large_scope":
                observed_scopes[identity["candidate_identity"]] = tuple(
                    member["path"] for member in source["repository"]
                )
                if source["context"].get("reviews"):
                    assert len(source["context"]["reviews"]) == 4
                    assert len(json.dumps(source["context"]).encode()) <= MAX_REVIEW_BYTES
                    assert all(
                        review["reviewed_paths"] == {"complete_scope": identity["scope_identity"]}
                        for review in source["context"]["reviews"]
                    )
            if scenario in ("cancel", "timeout") or (
                scenario == "cancel_repair" and len(observed) == 6
            ):
                entered.set()
                release.wait()
            member = next(m for m in source["repository"] if m["path"] == "repo/code.txt")
            candidate = identity["candidate_identity"]
            if 'schema="oh-goon.agent-review/v1"' in system:
                clean = member["content"] == "repaired\n"
                record = dict(
                    schema="oh-goon.agent-review/v1",
                    **identity,
                    reviewed_paths=[m["path"] for m in source["repository"]],
                    verdict="CLEAN" if clean else "FINDING",
                    summary="Scoped synthetic review",
                    findings=[]
                    if clean
                    else [
                        dict(
                            finding_id="f1",
                            explanation="Broken content",
                            evidence=[dict(path=member["path"], sha256=member["sha256"], line=1)],
                        )
                    ],
                )
                if scenario == "large_scope":
                    # Native-sized source names alone exceed the record budget;
                    # the explicit complete-scope claim does not narrow that scope.
                    assert len(json.dumps(record).encode()) > MAX_REVIEW_BYTES
                    record["reviewed_paths"] = {"complete_scope": identity["scope_identity"]}
                if (
                    scenario in ("leave_alone", "insufficient", "mismatched", "missing_coverage")
                    and len(observed) == 2
                ):
                    if scenario in ("leave_alone", "insufficient"):
                        record.update(
                            verdict="LEAVE_ALONE"
                            if scenario == "leave_alone"
                            else "INSUFFICIENT_EVIDENCE",
                            findings=[],
                        )
                    elif scenario == "mismatched":
                        record["candidate_identity"] = "different-candidate"
                    else:
                        record.pop("reviewed_paths")
                if scenario == "stale_candidate" and len(observed) == 4:
                    (repo / "code.txt").write_text("outside edit\n")
                if scenario == "configuration" and len(observed) == 4:
                    monkeypatch.setenv("TEST_REVIEW_VERIFICATION", "unreviewed-check")
            elif 'schema="oh-goon.agent-adjudication/v1"' in system:
                record = dict(
                    schema="oh-goon.agent-adjudication/v1",
                    candidate_identity=candidate,
                    scope_identity=identity["scope_identity"],
                    dispositions=[
                        dict(
                            reviewer=review["reviewer"],
                            finding_id=finding["finding_id"],
                            decision="repair",
                            rationale="Same scoped fix",
                        )
                        for review in source["context"]["reviews"]
                        for finding in review["findings"]
                    ],
                    queue=[
                        dict(
                            queue_id="q1",
                            findings=[
                                dict(reviewer=r["reviewer"], finding_id=f["finding_id"])
                                for r in source["context"]["reviews"]
                                for f in r["findings"]
                            ],
                            rationale="Shared causal root",
                            regression_expectation="Check repaired content",
                        )
                    ],
                )
                if scenario == "bad_adjudication":
                    record["dispositions"].pop()
            else:
                record = dict(
                    schema="agent-patch-batch-v1",
                    candidate_identity=candidate,
                    summary="Repair code and add regression evidence",
                    regressions=[
                        dict(
                            queue_id="q1",
                            path="repo/regression.txt",
                            expectation="Check repaired content",
                        )
                    ],
                    operations=[
                        dict(
                            action="update",
                            path="repo/code.txt",
                            source_sha256=member["sha256"],
                            replacement="repaired\n",
                        ),
                        dict(
                            action="create",
                            path="repo/regression.txt",
                            source_sha256=None,
                            replacement='#!/bin/sh\n[ "$(cat code.txt)" = repaired ]\n',
                        ),
                    ],
                )
                if scenario == "missing_regression":
                    record.pop("regressions")
                elif scenario == "scope":
                    record["operations"].append(
                        dict(
                            action="create",
                            path="repo/outside.txt",
                            source_sha256=None,
                            replacement="outside",
                        )
                    )
                elif scenario == "no_progress":
                    record["operations"].pop(0)
            payload = json.dumps(
                {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(record)}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                if scenario not in ("cancel", "cancel_repair", "timeout"):
                    raise

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=partial(server.serve_forever, poll_interval=0.01), daemon=True)
        worker.start()
        try:
            resolver, path, repo, options = agent_review_case(
                tmp_path,
                endpoint=f"http://127.0.0.1:{server.server_port}",
                plan_template=template_key,
            )
            if scenario in ("accepted_nonzero", "python_phases"):
                from oh_goon.runtime.goon_templates import GoonTemplateStore

                templates = GoonTemplateStore()
                catalog = tmp_path / "local-goons/agents"
                (catalog / "custom").mkdir(parents=True)
                composite = yaml.safe_load(
                    templates.source("agents/agentic-review-repair-loop").content
                )
                for edge in composite["goons"]:
                    if edge["phase"] in ("regression", "verification"):
                        edge["from"] = "../.."
                        if scenario == "python_phases":
                            edge["pass"] = ["candidate_directory"]
                if scenario == "python_phases":
                    composite["inputs"] = [
                        value
                        for value in composite["inputs"]
                        if value not in ("regression_command", "verification_command")
                    ]
                    options["inputs"].pop("regression_command")
                    options["inputs"].pop("verification_command")
                    composite["inputs"].append("candidate_directory")
                    options["inputs"]["candidate_directory"] = str(repo)
                (catalog / "custom/goon.yml").write_text(yaml.safe_dump(composite, sort_keys=False))
                (catalog / "custom/README.md").write_text("# agents/custom\n")
                for phase in ("regression", "verification"):
                    package = catalog / phase
                    if scenario == "python_phases":
                        write_isolated_build_project(package)
                        metadata = package / "pyproject.toml"
                        metadata.write_text(
                            metadata.read_text().replace(
                                "[project.scripts]",
                                'dependencies = ["absent-mode-probe==0.0.1"]\n[project.scripts]',
                            )
                        )
                        (package / "isolated_app.py").write_text(
                            "import argparse\n"
                            "from pathlib import Path\n"
                            "def main():\n"
                            "    parser = argparse.ArgumentParser()\n"
                            "    parser.add_argument('--candidate', required=True)\n"
                            "    candidate = Path(parser.parse_args().candidate)\n"
                            "    assert (candidate / 'code.txt').read_text() == 'repaired\\n'\n"
                            "    assert (candidate / 'regression.txt').is_file()\n"
                            "    print('native-python-phase-ran')\n"
                        )
                        content = {
                            "runtime": "python",
                            "uv": {"lock_mode": "frozen"},
                            "inputs": {"candidate_directory": {"type": "string"}},
                            "steps": [
                                {
                                    "run": "isolated-build --candidate ${{ inputs.candidate_directory }}"
                                }
                            ],
                        }
                    else:
                        package.mkdir()
                        content = yaml.safe_load(templates.source(f"agents/{phase}").content)
                        content["steps"][0]["success_codes"] = [0, 1]
                    (package / "goon.yml").write_text(yaml.safe_dump(content, sort_keys=False))
                    (package / "README.md").write_text(f"# agents/{phase}\n")
                path.write_text(
                    "execution: {purpose: verification}\ngoons:\n- import: agents/custom\n  from: local-goons\n"
                )
                if scenario == "accepted_nonzero":
                    check = repo / "check"
                    check.write_text(
                        check.read_text()
                        + 'result=$?\nif [ "$result" = 0 ]; then exit 1; fi\nexit "$result"\n'
                    )
            if scenario == "large_scope":
                cases = repo / "repository_evidence_and_verification_handoff_contracts"
                cases.mkdir()
                # Legal long names exercise the same record byte limit with fewer file operations.
                for index in range(256):
                    name = f"case_{index:04d}_" + "verification_evidence_contract_" * 6 + ".txt"
                    (cases / name).write_text("observed source\n")
                    large_scope_paths.add(f"repo/{cases.name}/{name}")
            if scenario == "remote":
                monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
                registry = yaml.safe_load((tmp_path / ".goonskills.yml").read_text())
                collection = tmp_path / "upstream"
                collection.mkdir()
                selected = []
                for skill_path in registry["local"].values():
                    directory = collection / skill_path
                    directory.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(tmp_path / skill_path), directory)
                    (directory / "guide.md").write_text("A supporting upstream resource.\n")
                    selected.append(skill_path)
                subprocess.run(["git", "init", "-q", "-b", "main", str(collection)], check=True)
                subprocess.run(["git", "-C", str(collection), "add", "."], check=True)
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(collection),
                        "-c",
                        "user.name=Test",
                        "-c",
                        "user.email=test@example.test",
                        "commit",
                        "-qm",
                        "Skills",
                    ],
                    check=True,
                )
                (tmp_path / ".goonskills.yml").write_text(
                    yaml.safe_dump(
                        {
                            "sources": {
                                "cookbook": {
                                    "source": collection.as_uri(),
                                    "ref": "main",
                                    "skills": selected,
                                },
                            }
                        }
                    )
                )
            if scenario == "configuration":
                from oh_goon.runtime.goon_templates import GoonTemplateStore

                authored = yaml.safe_load(
                    GoonTemplateStore().source("agents/agentic-review-repair-loop").content
                )
                authored.pop("inputs")
                authored["execution"] = {"purpose": "verification"}
                authored["goons"][-1]["with"] = {
                    "verification_command": {"from": {"env": "TEST_REVIEW_VERIFICATION"}}
                }
                path.write_text(yaml.safe_dump(authored, sort_keys=False))
                options["inputs"].pop("verification_command")
                monkeypatch.setenv("TEST_REVIEW_VERIFICATION", "check")
            if scenario in ("verification", "credentials"):
                (repo / "code.txt").write_text("repaired\n")
            if scenario == "credentials":
                monkeypatch.setenv("TEST_REVIEW_KEY", "fixture-secret-not-a-source")
                connection = options["connection"]
                options["connection"] = connection.model_copy(
                    update={
                        "provider": connection.provider.model_copy(
                            update={"api_key_env": "TEST_REVIEW_KEY"}
                        )
                    }
                )
            if scenario in ("verification", "regression"):
                script = "verify-fail" if scenario == "verification" else "regress-fail"
                (repo / script).write_text("#!/bin/sh\nexit 13\n")
                (repo / script).chmod(0o755)
                options["inputs"][
                    "verification_command" if scenario == "verification" else "regression_command"
                ] = script
            if scenario == "no_progress":
                (repo / "check").write_text("#!/bin/sh\nexit 0\n")
            if scenario == "arbitrary":
                catalog = tmp_path / "local-goons/agents/custom"
                catalog.mkdir(parents=True)
                from oh_goon.runtime.goon_templates import GoonTemplateStore

                content = GoonTemplateStore().source("agents/agentic-review-repair-loop").content
                registry = (tmp_path / ".goonskills.yml").read_text()
                for index, name in enumerate(
                    (
                        "single-source-of-truth-review",
                        "semantic-redecision-review",
                        "resolved-fact-regression-review",
                        "canonical-consumer-contract-review",
                    )
                ):
                    content = content.replace("cookbook/" + name, f"local-review-{index}")
                    registry = registry.replace("cookbook/" + name, f"local-review-{index}")
                authored = yaml.safe_load(content)
                authored["goons"][:4] = list(reversed(authored["goons"][:4]))
                content = yaml.safe_dump(authored, sort_keys=False)
                (catalog / "goon.yml").write_text(content)
                (catalog / "README.md").write_text("# agents/custom\n")
                (tmp_path / ".goonskills.yml").write_text(registry)
                path.write_text(
                    "execution: {purpose: verification}\ngoons:\n- import: agents/custom\n  from: local-goons\n"
                )
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "-c",
                    "user.email=test@example.com",
                    "-c",
                    "user.name=Test",
                    "commit",
                    "-qm",
                    "initial",
                ],
                check=True,
            )
            monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "database.sqlite"))
            monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
            monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
            lock_path = tmp_path / "goon.lock"
            if scenario in ("git_plan", "python_phases"):
                monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
                upstream = tmp_path / "git-plan"
                upstream.mkdir()
                authored = upstream / "workflows/review"
                authored.mkdir(parents=True)
                (authored / "plan.yml").write_bytes(path.read_bytes())
                if scenario == "python_phases":
                    shutil.copytree(tmp_path / "local-goons", authored / "local-goons")
                subprocess.run(["git", "init", "-q", "-b", "main", str(upstream)], check=True)
                subprocess.run(["git", "-C", str(upstream), "add", "."], check=True)
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(upstream),
                        "-c",
                        "user.name=Test",
                        "-c",
                        "user.email=test@example.test",
                        "commit",
                        "-qm",
                        "Plan",
                    ],
                    check=True,
                )
                commit = subprocess.check_output(
                    ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True
                ).strip()
                pin = LockedExecutionSource(
                    git=str(upstream),
                    ref="main",
                    path="workflows/review",
                    kind="plan",
                    plan="plan.yml",
                    commit=commit,
                )
                with prepare_git_source(pin.git, pin.ref, commit=pin.commit) as (checkout, _):
                    root = checkout / pin.path
                    graph = resolver.analyze_source(
                        root / "plan.yml",
                        containment_root=root,
                        plan_source=pin,
                        locked_sources=(pin,),
                    )
                path = graph.root.path
                assert not path.exists()
                options["source_graph"] = graph
            plan, deliveries, _ = resolver.resolve_frozen_agents(path, **options)
            assert "fixture-secret-not-a-source" not in plan.model_dump_json()
            assert all(
                b"fixture-secret-not-a-source" not in handoff.encode_prework_delivery()
                for handoff in deliveries.values()
            )
            write_execution_lock(lock_path, build_execution_lock(plan))
            lock = lock_path.read_bytes()
            if scenario in ("git_plan", "python_phases"):
                (authored / "plan.yml").write_text("goons:\n- python/quality\n")
                subprocess.run(["git", "-C", str(upstream), "add", "."], check=True)
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(upstream),
                        "-c",
                        "user.name=Test",
                        "-c",
                        "user.email=test@example.test",
                        "commit",
                        "-qm",
                        "Advance main",
                    ],
                    check=True,
                )
                shutil.rmtree(tmp_path / "cache/oh-goon/skill-sources")
            if scenario == "remote":
                assert len(plan.skill_sources) == 1
                assert all(
                    "guide.md" in {m.path for m in delivery.source.skill.members}
                    for delivery in deliveries.values()
                    if delivery.source.authored_skill_reference.startswith("cookbook/")
                )
                # A lost cache must restore the locked resources; the source
                # selector may move between the initial capture and execution.
                collection = tmp_path / "upstream"
                (collection / selected[0] / "SKILL.md").write_text("# A later branch revision\n")
                subprocess.run(["git", "-C", str(collection), "add", "."], check=True)
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(collection),
                        "-c",
                        "user.name=Test",
                        "-c",
                        "user.email=test@example.test",
                        "commit",
                        "-qm",
                        "Advance main",
                    ],
                    check=True,
                )
                shutil.rmtree(tmp_path / "cache/oh-goon/skill-sources")
            runtime = ApplicationRuntime.build(standard_local=True)
            try:
                runtime.locations.add(repo.parent)
                if scenario == "shared_http":
                    providers = tmp_path / "providers.toml"
                    providers.write_text(f"""default_model = "review"
[providers.test]
api = "openai-chat-completions"
base_url = "http://127.0.0.1:{server.server_port}"
[models.review]
provider = "test"
model = "synthetic/reviewer"
max_output_tokens = 8192
""")
                    monkeypatch.setenv("OH_GOON_PROVIDERS_FILE", str(providers))
                    document = runtime.shared_plan.save("shared-loop", path.read_text())
                    app = create_app(local_access_token="shared-campaign-test-token")
                    app.dependency_overrides[dependencies.shared_plan_service] = (
                        lambda: runtime.shared_plan
                    )
                    with closing(authenticated_api_client(app)) as client:
                        selected = dict(
                            plan_key=document.plan_key,
                            expected_revision=document.sha256,
                            repository_ids=[repository_id(repo)],
                            inputs=options["inputs"],
                            agent=dict(
                                model_profile="review",
                                acceptance="agentic-review",
                                repository_root=str(options["repository_root"]),
                                skill_root=str(tmp_path),
                            ),
                        )
                        preview = client.post("/api/league/shared-plan-previews", json=selected)
                        assert preview.status_code == 200, preview.text
                        reviewed = preview.json()
                        request = {
                            key: value
                            for key, value in selected.items()
                            if key != "expected_revision"
                        }
                        request.update(
                            expected_plan_revision=document.sha256,
                            expected_plan_id=reviewed["plan_id"],
                            expected_execution_digest=reviewed["execution_digest"],
                            repository_instance_ids={
                                row["repository"]["repository_id"]: row["repository_instance_id"]
                                for row in reviewed["teams"]
                            },
                            fleet_run_id="native-review-loop",
                            authorize_repair=True,
                            repair=dict(
                                review_passes=passes,
                                write_roots=["code.txt", "regression.txt"],
                                timeout_seconds=45,
                                step_timeout_seconds=10,
                            ),
                        )
                        response = client.post("/api/league/shared-plan-repairs", json=request)
                        assert response.status_code == 200, response.text
                        assert response.json()["result"] == "converged", json.dumps(
                            response.json(), indent=2
                        )
                    result = runtime.runs.agent_campaign_result("native-review-loop")
                    assert all(
                        isinstance(
                            item.run.target.source,
                            SharedPlanSource,
                        )
                        for item in result.attempts
                    )
                else:
                    result = runtime.plans.repair(
                        path,
                        source_graph=options.get("source_graph"),
                        lock_path=lock_path,
                        repository_id=repository_id(repo),
                        budget=AgentRepairBudget(
                            max_attempts=passes,
                            timeout_seconds=45,
                            # Exercise a stalled model request after cold producer
                            # startup, rather than racing that startup on busy hosts.
                            step_timeout_seconds=5 if scenario == "timeout" else 10,
                        ),
                        inputs=options["inputs"],
                        agent_repository_root=options["repository_root"],
                        agent_skill_root=options["skill_root"],
                        allowed_agent_acceptance=options["allowed_acceptance"],
                        agent_connection=options["connection"],
                        review_write_roots=("code.txt", "regression.txt"),
                        fleet_run_id="native-review-loop",
                        cancel_requested=(lambda: entered.is_set())
                        if scenario.startswith("cancel")
                        else None,
                    )
                output = (
                    runtime.runs.game_tape_live_output(result.admitted_run_ids[-1])
                    if result.admitted_run_ids
                    else None
                )
                assert result.stop_reason.value == expected, (
                    result.message
                    + "\n"
                    + ("\n".join(chunk.text for chunk in output.chunks) if output else str(result))
                )
                assert result.test_passed is (expected == "converged")
                if scenario == "accepted_nonzero":
                    accepted = [
                        step
                        for detail in result.attempts
                        if detail.run.target.agent_repair.phase in ("regression", "verification")
                        for step in detail.operations[0].evidence.plan.steps
                    ]
                    assert len(accepted) == 2
                    assert all(step.returncode == 1 for step in accepted)
                    assert all(step.settlement.outcome == "SUCCESS" for step in accepted)
                    assert all(step.settlement.cleanup == "VERIFIED" for step in accepted)
                phases = [detail.run.target.agent_repair.phase for detail in result.attempts]
                assert phases == expected_phases
                assert len(observed) == calls
                review_steps = [
                    step.step_id
                    for step in plan.steps
                    if step.kind == "agent" and step.workload.harness == "provider-review"
                ]
                assert [o["reviewer"] for o in observed[: min(calls, 4)]] == review_steps[
                    : min(calls, 4)
                ]
                assert len({o["candidate_identity"] for o in observed[:4]}) == 1
                assert lock_path.read_bytes() == lock
                for index, detail in enumerate(result.attempts):
                    if scenario in ("git_plan", "python_phases"):
                        assert detail.run.target.plan.execution_sources == (pin,)
                    context = detail.run.target.agent_repair
                    assert context.max_attempts == 4 * (passes - 1) + 2
                    assert context.previous_run_id == (
                        result.attempts[index - 1].run.fleet_run_id if index else None
                    )
                    assert detail.run.status.terminal
                    assert not detail.rerun_supported
                    assert runtime.runs.get(detail.run.fleet_run_id).operations == detail.operations
                    assert "fixture-secret-not-a-source" not in detail.model_dump_json()
                    if scenario == "python_phases":
                        assert detail.run.target.plan.configuration is not None
                        phase = detail.run.target.agent_repair.phase
                        if phase in ("regression", "verification"):
                            selected = detail.run.target.plan.steps[0]
                            assert selected.uv.lock_mode == "frozen"
                            assert selected.project_source is not None
                            recorded = detail.operations[0].evidence.plan.steps[0]
                            assert recorded.settlement.outcome == "SUCCESS"
                            assert recorded.attempts[0].uv_preparation.lock_mode == "frozen"
                            assert recorded.attempts[0].uv_preparation.freshness == "not_checked"
                if scenario == "large_scope":
                    records = [
                        observation.record
                        for detail in result.attempts
                        for step in detail.operations[0].evidence.plan.steps
                        for observation in step.observations
                        if isinstance(observation, AgentReviewObservation)
                        and isinstance(observation.record, AgentReviewRecord)
                    ]
                    assert len(records) == 8
                    assert [record.verdict for record in records] == ["FINDING"] * 4 + ["CLEAN"] * 4
                    assert set(observed_scopes[records[0].candidate_identity]) == {
                        "repo/code.txt",
                        "repo/check",
                        *large_scope_paths,
                    }
                    for record in records:
                        assert record.reviewed_paths == observed_scopes[record.candidate_identity]
                if scenario in (
                    "repair",
                    "shared_http",
                    "git_plan",
                    "python_phases",
                    "accepted_nonzero",
                    "remote",
                    "arbitrary",
                    "regression",
                    "large_scope",
                ):
                    assert (repo / "code.txt").read_text() == "repaired\n"
                    assert (repo / "regression.txt").is_file()
                    if scenario != "regression":
                        assert (
                            observed[-1]["candidate_identity"] != observed[0]["candidate_identity"]
                        )
                elif scenario in (
                    "budget",
                    "leave_alone",
                    "insufficient",
                    "mismatched",
                    "missing_coverage",
                    "bad_adjudication",
                    "missing_regression",
                    "scope",
                    "cancel",
                    "cancel_repair",
                    "timeout",
                ):
                    assert (repo / "code.txt").read_text() == "broken\n"
                    assert not (repo / "regression.txt").exists()
            finally:
                runtime.shutdown(wait_for_runs=True)
            if scenario in (
                "repair",
                "accepted_nonzero",
                "large_scope",
                "shared_http",
                "git_plan",
                "python_phases",
            ):
                reopened = ApplicationRuntime.build(standard_local=True)
                try:
                    assert [
                        reopened.runs.get(identifier).run.target.agent_repair.phase
                        for identifier in result.admitted_run_ids
                    ] == expected_phases
                    assert len(observed) == calls
                    if scenario == "python_phases":
                        for identifier in result.admitted_run_ids:
                            restored = reopened.runs.get(identifier)
                            assert restored.run.target.plan.execution_sources == (pin,)
                            assert restored.run.target.plan.configuration is not None
                            if restored.run.target.agent_repair.phase in (
                                "regression",
                                "verification",
                            ):
                                assert restored.run.target.plan.steps[0].uv.lock_mode == "frozen"
                                assert (
                                    restored.operations[0]
                                    .evidence.plan.steps[0]
                                    .attempts[0]
                                    .uv_preparation.freshness
                                    == "not_checked"
                                )
                    if scenario == "accepted_nonzero":
                        campaign = reopened.runs.agent_campaign(result.repair_id)
                        assert campaign["result"] == "converged"
                        assert len(campaign["verification"]) == 2
                        assert all(
                            row["unverified_step_ids"] == []
                            and row["recorded_steps"][0]["exit_code"] == 1
                            for row in campaign["verification"]
                        )
                finally:
                    reopened.shutdown(wait_for_runs=True)
        finally:
            release.set()
            server.shutdown()
            worker.join(timeout=5)


_PROTECTED_PHASE_CODEX = r"""#!/usr/bin/python3
import json,os,pathlib,sys,urllib.request
prompt=sys.stdin.buffer.read(); data=json.loads(prompt)
for index,argument in enumerate(sys.argv):
    if argument=='-c' and sys.argv[index+1].startswith('model_instructions_file='):
        assert pathlib.Path(json.loads(sys.argv[index+1].split('=',1)[1])).read_bytes()==prompt
identity=data['identity']; context=data['context']; contract=data['output_contract']
for root in (pathlib.Path(data['working_workspace']['root']),pathlib.Path('/workspace')):
    for target in ('src/code.txt','.git/config','outside-agent-effect'):
        try: (root/target).write_text('UNAUTHORIZED')
        except OSError: pass
        else: raise AssertionError('native reasoning obtained source writes')
assert not (pathlib.Path(os.environ['CODEX_HOME'])/'auth.json').exists()
assert 'PROTECTED_CAMPAIGN_KEY' not in os.environ
request=urllib.request.Request('http://127.0.0.1:18743/v1/responses',
    data=json.dumps(dict(model='synthetic-native',input=[dict(role='user',content=prompt.decode())])).encode(),
    headers={'Content-Type':'application/json','Authorization':'Bearer oh-goon-local'})
with urllib.request.urlopen(request,timeout=10) as response: assert json.load(response)['fixture']
pathlib.Path(os.environ['TMPDIR'],'native-disposable-state').write_text('scratch')
source=(pathlib.Path(data['captured_repository'])/'src/code.txt').read_text()
manifest=json.loads(pathlib.Path(data['complete_source_manifest']).read_text())
member=next(m for m in manifest['repository'] if m['path']=='repo/src/code.txt')
if 'oh-goon.agent-review/v1' in contract:
    clean=source=='repaired\n'
    record=dict(schema='oh-goon.agent-review/v1',**identity,verdict='CLEAN' if clean else 'FINDING',
        summary='Complete review with physical source denial',
        reviewed_paths={'complete_scope':identity['scope_identity']},findings=[] if clean else [
            dict(finding_id='f1',explanation='Reproduced broken content',
                evidence=[dict(path=member['path'],sha256=member['sha256'],line=1)])])
elif 'oh-goon.agent-adjudication/v1' in contract:
    refs=[dict(reviewer=r['reviewer'],finding_id=f['finding_id']) for r in context['reviews'] for f in r['findings']]
    record=dict(schema='oh-goon.agent-adjudication/v1',candidate_identity=identity['candidate_identity'],
        scope_identity=identity['scope_identity'],dispositions=[dict(**ref,decision='repair',rationale='One reproduced defect') for ref in refs],
        queue=[dict(queue_id='q1',findings=refs,rationale='One causal root',regression_expectation='Verify repaired content')])
else:
    record=dict(schema='agent-patch-batch-v1',candidate_identity=identity['candidate_identity'],summary='Repair admitted source and behavioral check',
        regressions=[dict(queue_id='q1',path='repo/tests/regression.txt',expectation='Require repaired content')],
        operations=[dict(action='update',path='repo/src/code.txt',source_sha256=member['sha256'],replacement='repaired\n'),
            dict(action='create',path='repo/tests/regression.txt',source_sha256=None,replacement='#!/bin/sh\n[ "$(cat src/code.txt)" = repaired ]\n')])
print('RAW_PROTECTED_NATIVE_OUTPUT_CANARY',file=sys.stderr)
print(json.dumps(dict(type='turn.started')))
print(json.dumps(dict(type='item.completed',item=dict(type='agent_message',text=json.dumps(record)))))
print(json.dumps(dict(type='turn.completed')))
"""


@pytest.mark.process
@pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bwrap") is None, reason="Linux Bubblewrap required"
)
def test_protected_native_campaign_keeps_exact_phase_scope_through_real_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, installed_cli: None
) -> None:
    """A deterministic native protocol exercises real permission/admission transitions."""
    public = tmp_path / "public-native"
    public.mkdir()
    program = public / "codex"
    program.write_text(_PROTECTED_PHASE_CODEX)
    program.chmod(0o755)
    observed: list[dict[str, object]] = []
    monkeypatch.setenv("PROTECTED_CAMPAIGN_KEY", "synthetic-host-only-campaign-key")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.headers.get("Authorization") == "Bearer synthetic-host-only-campaign-key"
            assert request["model"] == "synthetic-native"
            context = request["input"][0]
            assert context["role"] == "developer" and "Hashmarks" in context["content"]
            assert "Skill instructions" in context["content"]
            assert "synthetic-host-only-campaign-key" not in json.dumps(request)
            observed.append(request)
            payload = b'{"fixture":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=partial(server.serve_forever, poll_interval=0.01))
        worker.start()
        try:
            backend = BubblewrapProcessIsolation(shutil.which("bwrap") or "")
            selected = ResolvedNativeAgent(
                agent="codex",
                profile_name="protected-phase-fixture",
                definition=NativeAgentDefinition(
                    executable=str(program), runtime_roots=(str(public),)
                ),
                profile=NativeAgentProfile(
                    backend="bubblewrap",
                    sandbox_owner="execution_backend",
                    allowed_agents=("codex",),
                    inference_profile="selected",
                ),
                executable_sha256=native_executable_sha256(program),
                connection=AgentConnection.select(
                    AgentProvidersConfig.model_validate(
                        {
                            "providers": {
                                "synthetic": {
                                    "api": "openai-responses",
                                    "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                                    "api_key_env": "PROTECTED_CAMPAIGN_KEY",
                                }
                            },
                            "models": {
                                "selected": {"provider": "synthetic", "model": "synthetic-native"}
                            },
                        }
                    ),
                    "selected",
                ),
                backend_configuration_id=backend.configuration_identity(),
            )
            resolver, path, repo, options = agent_review_case(tmp_path, native_agent=selected)
            (repo / "src").mkdir()
            (repo / "tests").mkdir()
            (repo / "code.txt").rename(repo / "src/code.txt")
            (repo / "check").write_text(
                "#!/bin/sh\nset -eu\n[ ! -e /config/native/auth.json ]\n"
                'if [ -f tests/regression.txt ]; then /bin/sh tests/regression.txt; else [ "$(cat src/code.txt)" = repaired ]; fi\n'
            )
            # Reuse the real phase Goons as project imports, selected by the normal resolver.
            authored = yaml.safe_load(
                (tmp_path / "native-goons/agents/agentic-review-repair-loop/goon.yml").read_text()
            )
            authored["execution"] = {"purpose": "verification"}
            for edge in authored["goons"]:
                edge["from"] = "native-goons"
            path.write_text(yaml.safe_dump(authored, sort_keys=False))
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            index_before = (repo / ".git/index").read_bytes()
            monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state/app.sqlite"))
            monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
            monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
            monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
            from oh_goon.services.run_configuration import load_run_configuration

            configuration = tmp_path / "run.yml"
            configuration.write_text(
                yaml.safe_dump(
                    {
                        "version": 1,
                        "source": {"kind": "file-plan", "path": "plan.yml"},
                        "target": {"repository_id": repository_id(repo)},
                        "inputs": options["inputs"],
                        "agent": {
                            "implementation": selected.agent,
                            "profile": selected.profile_name,
                            "acceptance": "agentic-review",
                            "repository_root": str(options["repository_root"]),
                            "skill_root": ".",
                        },
                        "repair": {
                            "review_passes": 2,
                            "write_roots": ["src", "tests"],
                            "timeout_seconds": 180,
                            "step_timeout_seconds": 30,
                        },
                    }
                )
            )
            snapshot = load_run_configuration(configuration)
            assert snapshot.selection.repair is not None
            options["inputs"] = snapshot.inputs
            graph = resolver.analyze_source(path)
            plan, handoffs, _ = resolver.resolve_frozen_agents(path, source_graph=graph, **options)
            execution_lock = build_execution_lock(
                plan, external_artifacts=snapshot.external_artifacts()
            )
            write_execution_lock(path.parent / "goon.lock", execution_lock)
            lock_before = (path.parent / "goon.lock").read_bytes()
            runner = native_profile_command_runner(
                repo, selected, backend, RepositoryWorkspaceAuthority.pin_repository_subject
            )
            git = GitService(
                command_runner=runner, observation_runner=partial(runner, read_only_workspace=True)
            )
            runtime = ApplicationRuntime.build(
                standard_local=True, standard_process_isolation=backend, git_service=git
            )
            try:
                runtime.locations.add(repo.parent)
                result = runtime.plans.repair(
                    path,
                    repository_id=repository_id(repo),
                    budget=snapshot.selection.repair.budget(),
                    inputs=options["inputs"],
                    agent_repository_root=options["repository_root"],
                    agent_skill_root=options["skill_root"],
                    allowed_agent_acceptance=options["allowed_acceptance"],
                    review_write_roots=snapshot.selection.repair.write_roots,
                    native_agent=selected,
                    native_workspace=repo,
                    repository=repo,
                    source_graph=graph,
                    lock_path=snapshot.lock_path,
                    external_artifacts=snapshot.external_artifacts(),
                    caller_input_file=snapshot.authority(),
                )
                assert result.stop_reason.value == "converged", result.message
                assert len(observed) == 10
                assert len(set(result.admitted_run_ids)) == 6 and not result.unresolved_run_ids
                phases: list[str] = []
                step_count = 0
                records: list[AgentReviewRecord] = []
                proposal_outputs: tuple[str, ...] | None = None
                for index, detail in enumerate(result.attempts):
                    assert isinstance(detail.run.target, FleetPlanTarget)
                    assert detail.run.target.lock_artifacts == execution_lock.external
                    assert detail.run.target.caller_input_file == snapshot.authority()
                    context = detail.run.target.agent_repair
                    assert context is not None and context.phase is not None
                    phases.append(context.phase)
                    operation = detail.operations[0].evidence
                    assert (
                        isinstance(operation, FleetPlanOperationEvidence)
                        and operation.plan is not None
                    )
                    phase_steps = operation.plan.steps
                    step_count += len(phase_steps)
                    if context.phase == "repair":
                        proposal_outputs = tuple(output.name for output in phase_steps[0].outputs)
                    assert context.previous_run_id == (
                        result.admitted_run_ids[index - 1] if index else None
                    )
                    assert context.original_execution_digest == execution_lock.digest
                    assert detail.outcome.value == "SUCCESS" and detail.run.status.terminal
                    assert runtime.runs.get(detail.run.fleet_run_id).operations == detail.operations
                    live = runtime.runs.game_tape_live_output(detail.run.fleet_run_id)
                    assert "RAW_PROTECTED_NATIVE_OUTPUT_CANARY" not in "".join(
                        chunk.text for chunk in live.chunks
                    )
                    for step in phase_steps:
                        assert isinstance(step, CompletedPlanStepExecution)
                        assert step.returncode == 0 and len(step.attempts) == 1
                        attempt = step.attempts[0]
                        isolation = attempt.standard_isolation
                        assert isolation is not None and isolation.workspace_read_only
                        assert isolation.filesystem_cleanup == "verified"
                        assert attempt.supervisor is not None
                        assert attempt.supervisor.final_process_group_quiescent is True
                        native = step.kind == "agent"
                        assert isolation.credential_visibility == "none"
                        assert isolation.host_and_external_ip_networking == "denied"
                        assert isolation.broker_socket_mounted is native
                        assert isolation.scoped_write_roots == (
                            ("src", "tests") if context.phase == "repair" and not native else ()
                        )
                        assert not step.artifacts
                        records.extend(
                            o.record
                            for o in step.observations
                            if isinstance(o, AgentReviewObservation)
                            and isinstance(o.record, AgentReviewRecord)
                        )
                assert phases == [
                    "review",
                    "adjudicate",
                    "repair",
                    "regression",
                    "review",
                    "verification",
                ]
                assert step_count == 13
                assert [record.verdict for record in records] == ["FINDING"] * 4 + ["CLEAN"] * 4
                assert (
                    records[0].candidate_identity
                    == next(iter(handoffs.values())).source.repository.content_identity
                )
                assert records[-1].candidate_identity != records[0].candidate_identity
                assert "repo/tests/regression.txt" in records[-1].reviewed_paths
                assert proposal_outputs == ("proposal",)
                assert (repo / "src/code.txt").read_text() == "repaired\n"
                assert (repo / "tests/regression.txt").is_file()
                assert (repo / ".git/index").read_bytes() == index_before
                assert (path.parent / "goon.lock").read_bytes() == lock_before
            finally:
                runtime.shutdown(wait_for_runs=True)

        finally:
            server.shutdown()
            worker.join(timeout=5)
        assert not worker.is_alive()


_CRASHING_CAMPAIGN_CONTROLLER = r"""
import json
import os
import sys
from pathlib import Path

from oh_goon.application import ApplicationRuntime
from oh_goon.core.models.agent_providers import AgentConnection
from oh_goon.core.models.agent_repair import AgentRepairBudget, AgentRepairStopReason
from oh_goon.core.product_identity import repository_id

selected = json.loads(sys.argv[1])

# Keep original product admission, process publication and effect execution;
# inject only a real controller death at an existing owner boundary.
if selected["cut"] == "admission":
    from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane

    start_phase = FleetRunControlPlane.start_prepared_plan

    def crash_after_admission(self, *args, **kwargs):
        start_phase(self, *args, **kwargs)
        os._exit(84)

    FleetRunControlPlane.start_prepared_plan = crash_after_admission
elif selected["cut"] == "process":
    from oh_goon.core.vocabulary import FleetRunEventType
    from oh_goon.runtime.journal import Journal

    record_process = Journal.record_execution_lifecycle

    def crash_after_process_publication(self, *args, **kwargs):
        record_process(self, *args, **kwargs)
        if kwargs["event_type"] is FleetRunEventType.PROCESS_STARTED:
            os._exit(85)

    Journal.record_execution_lifecycle = crash_after_process_publication
elif selected["cut"] == "apply":
    from oh_goon.runtime.fault_injection import FaultPoint
    from oh_goon.services.repository_effect_execution import RepositoryEffectExecution

    class CrashAfterAppliedEffect:
        def hit(self, point):
            repository = Path(selected["repository"])
            if (
                point is FaultPoint.BEFORE_OPERATION_COMMIT
                and (repository / "code.txt").read_text() == "repaired\n"
                and (repository / "regression.txt").is_file()
            ):
                os._exit(86)

    initialize_effects = RepositoryEffectExecution.__init__

    def initialize_crashing_effects(self, *args, **kwargs):
        kwargs["faults"] = CrashAfterAppliedEffect()
        initialize_effects(self, *args, **kwargs)

    RepositoryEffectExecution.__init__ = initialize_crashing_effects

runtime = ApplicationRuntime.build(standard_local=True)
runtime.locations.add(Path(selected["repository_root"]))
commit = runtime.runs.commit_agent_campaign_decision

def crash_at_publication(decision):
    if decision.stop_reason is AgentRepairStopReason.CONVERGED:
        if selected["cut"] == "after":
            commit(decision)
        os._exit(81 if selected["cut"] == "before" else 82)
    return commit(decision)

runtime.runs.commit_agent_campaign_decision = crash_at_publication
result = result = runtime.plans.repair(
    Path(selected["plan"]), repository_id=repository_id(Path(selected["repository"])),
    budget=AgentRepairBudget(max_attempts=2, timeout_seconds=45, step_timeout_seconds=10),
    inputs=selected["inputs"], agent_repository_root=Path(selected["repository_root"]),
    agent_skill_root=Path(selected["skill_root"]), allowed_agent_acceptance=("agentic-review",),
    agent_connection=AgentConnection.model_validate(selected["connection"]),
    review_write_roots=tuple(selected.get("write_roots", ("code.txt",))), fleet_run_id="crash-campaign",
)
print(result.stop_reason.value, file=sys.stderr, flush=True)
os._exit(83)
"""


@pytest.mark.process
@pytest.mark.parametrize("cut", ["before", "after"])
def test_controller_death_around_final_publication_reopens_original_result(
    tmp_path, monkeypatch, cut
):
    """Detect inferring convergence from verification or losing a committed decision.

    The child executes original Fleet admission, real provider subprocesses and
    native verification. Only the death cut is injected; stdout is discarded.
    """
    from oh_goon.core.errors import JournalConflict, StateConflict
    from oh_goon.core.projections.agent_campaign import project_agent_campaign
    from oh_goon.runtime.database import LocalDatabase
    from oh_goon.runtime.journal import Journal
    from oh_goon.runtime.local_state_maintenance import reset_local_state

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            source = json.loads(payload["messages"][1]["content"])
            requests.append(source["identity"]["reviewer"])
            record = dict(
                schema="oh-goon.agent-review/v1",
                **source["identity"],
                verdict="CLEAN",
                summary="Complete current-candidate review",
                reviewed_paths=[member["path"] for member in source["repository"]],
                findings=[],
            )
            response = json.dumps(
                {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(record)}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=partial(server.serve_forever, poll_interval=0.01))
        worker.start()
        try:
            resolver, path, repo, options = agent_review_case(
                tmp_path, endpoint=f"http://127.0.0.1:{server.server_port}"
            )
            (repo / "code.txt").write_text("repaired\n")
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            plan, _, _ = resolver.resolve_frozen_agents(path, **options)
            write_execution_lock(path.parent / "goon.lock", build_execution_lock(plan))
            database_path = tmp_path / "state/database.sqlite"
            monkeypatch.setenv("OH_GOON_DB", str(database_path))
            monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
            monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
            selected = dict(
                cut=cut,
                plan=str(path),
                repository=str(repo),
                repository_root=str(options["repository_root"]),
                skill_root=str(options["skill_root"]),
                inputs=options["inputs"],
                connection=options["connection"].model_dump(mode="json"),
            )
            child = subprocess.run(
                [sys.executable, "-c", _CRASHING_CAMPAIGN_CONTROLLER, json.dumps(selected)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=60,
                check=False,
            )
            assert child.returncode == (81 if cut == "before" else 82), child.stderr.decode()
            assert len(requests) == 4
            database = LocalDatabase(database_path)
            journal = Journal(database)
            view = journal.read_view()
            campaign = view.campaign("crash-campaign")
            details = view.run_details()
            assert [phase.context.phase for phase in campaign.phases] == [
                "review",
                "verification",
            ]
            assert len(details) == 2 and all(
                detail.outcome.value == "SUCCESS" for detail in details
            )
            assert all(detail.campaign_journal == campaign for detail in details)
            assert all(detail.game_tape.campaign_journal == campaign for detail in details)
            report = project_agent_campaign("crash-campaign", details, campaign=campaign)
            if cut == "before":
                assert campaign.terminal_decision is None
                assert report["result"] == "incomplete"
                assert report["controller_result"]["availability"] == "unavailable"
                with pytest.raises(JournalConflict, match="missing or incomplete"):
                    journal.agent_campaign_result("crash-campaign")
                with pytest.raises(StateConflict, match="incomplete campaign"):
                    reset_local_state(database_path)
                assert database_path.is_file()
            else:
                receipt = journal.agent_campaign_result("crash-campaign")
                assert receipt.converged and receipt.committed_decision_id
                assert report["result"] == "converged"
                assert report["controller_result"]["decision_id"] == receipt.committed_decision_id
            assert journal.read_view().revision == view.revision
            assert len(requests) == 4  # Reading did not resume or rerun native work.
        finally:
            server.shutdown()
            worker.join(timeout=5)
        assert not worker.is_alive()


def _assert_started_crash_processes_exited(details):
    """Use bound native process identities; never turn observation into cleanup truth."""
    import select
    import signal

    from oh_goon.core.models.run_events import ProcessStartedEvent
    from oh_goon.core.process_birth import observe_process_birth

    started = [
        event
        for detail in details
        for event in detail.lifecycle_events
        if type(event) is ProcessStartedEvent
    ]
    for event in started:
        assert event.process_identity is not None
        try:
            descriptor = os.pidfd_open(event.pid)
        except ProcessLookupError:
            continue
        try:
            # Bind the descriptor before observing birth so PID reuse cannot
            # redirect a cleanup signal to an unrelated process.
            birth = observe_process_birth(event.pid)
            if birth is not None and (
                f"{birth.boot_id}:{birth.start_ticks}" != event.process_identity
            ):
                continue  # The recorded process exited; its PID was reused.
            # pidfd readiness is the native exit barrier; no sleep-based race.
            if not select.select([descriptor], [], [], 5)[0]:
                assert birth is not None, "Cannot bind the recorded process for cleanup"
                try:
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                except ProcessLookupError:
                    pass  # Exit raced the failed parent-death deadline.
                pytest.fail("Original parent-death guard did not stop its recorded process")
        finally:
            os.close(descriptor)
    return started


@pytest.mark.process
@pytest.mark.skipif(sys.platform != "linux", reason="Linux parent-death and pidfd evidence")
@pytest.mark.parametrize("cut,exit_code", [("admission", 84), ("process", 85), ("apply", 86)])
def test_controller_death_during_admission_process_and_applied_effect(
    tmp_path,
    monkeypatch,
    installed_cli,
    cut,
    exit_code,
):
    """Retain actual effects and unresolved lineage without replay or invented success."""
    from oh_goon.core.errors import (
        JournalConflict,
        RepositoryEffectQuarantined,
        RuntimeSafetyBlocked,
        StateConflict,
    )
    from oh_goon.core.models.run_events import DescendantsQuiescentEvent
    from oh_goon.core.projections.agent_campaign import project_agent_campaign
    from oh_goon.runtime.database import LocalDatabase
    from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
    from oh_goon.runtime.journal import Journal
    from oh_goon.runtime.local_state_maintenance import reset_local_state

    observed = []
    controller_dead = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            source = json.loads(request["messages"][1]["content"])
            identity = source["identity"]
            observed.append(identity["reviewer"])
            if cut != "apply":
                # Admission/process death must not depend on a fast model reply.
                # The HTTP fixture releases only after the actual child exits.
                assert controller_dead.wait(15)
            member = next(item for item in source["repository"] if item["path"] == "repo/code.txt")
            system = request["messages"][0]["content"]
            if 'schema="oh-goon.agent-review/v1"' in system:
                record = dict(
                    schema="oh-goon.agent-review/v1",
                    **identity,
                    verdict="FINDING",
                    summary="Exact original source requires the scoped repair",
                    reviewed_paths=[item["path"] for item in source["repository"]],
                    findings=[
                        dict(
                            finding_id="original-defect",
                            explanation="Broken original source",
                            evidence=[dict(path=member["path"], sha256=member["sha256"], line=1)],
                        )
                    ],
                )
            elif 'schema="oh-goon.agent-adjudication/v1"' in system:
                records = source["context"]["reviews"]
                record = dict(
                    schema="oh-goon.agent-adjudication/v1",
                    candidate_identity=identity["candidate_identity"],
                    scope_identity=identity["scope_identity"],
                    dispositions=[
                        dict(
                            reviewer=review["reviewer"],
                            finding_id=finding["finding_id"],
                            decision="repair",
                            rationale="Original bounded defect",
                        )
                        for review in records
                        for finding in review["findings"]
                    ],
                    queue=[
                        dict(
                            queue_id="q1",
                            rationale="One source defect",
                            regression_expectation="Check repaired content",
                            findings=[
                                dict(reviewer=review["reviewer"], finding_id=finding["finding_id"])
                                for review in records
                                for finding in review["findings"]
                            ],
                        )
                    ],
                )
            else:
                record = dict(
                    schema="agent-patch-batch-v1",
                    candidate_identity=identity["candidate_identity"],
                    summary="Repair actual code and create independent regression evidence",
                    regressions=[
                        dict(
                            queue_id="q1",
                            path="repo/regression.txt",
                            expectation="Check repaired content",
                        )
                    ],
                    operations=[
                        dict(
                            action="update",
                            path="repo/code.txt",
                            source_sha256=member["sha256"],
                            replacement="repaired\n",
                        ),
                        dict(
                            action="create",
                            path="repo/regression.txt",
                            source_sha256=None,
                            replacement='#!/bin/sh\n[ "$(cat code.txt)" = repaired ]\n',
                        ),
                    ],
                )
            response = json.dumps(
                {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(record)}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            try:
                self.wfile.write(response)
            except (BrokenPipeError, ConnectionResetError):
                assert cut != "apply"

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=partial(server.serve_forever, poll_interval=0.01))
        worker.start()
        try:
            resolver, path, repo, options = agent_review_case(
                tmp_path, endpoint=f"http://127.0.0.1:{server.server_port}"
            )
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            plan, _, _ = resolver.resolve_frozen_agents(path, **options)
            write_execution_lock(path.parent / "goon.lock", build_execution_lock(plan))
            lock_before = (path.parent / "goon.lock").read_bytes()
            database_path = tmp_path / "state/database.sqlite"
            monkeypatch.setenv("OH_GOON_DB", str(database_path))
            monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
            monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
            selected = dict(
                cut=cut,
                plan=str(path),
                repository=str(repo),
                repository_root=str(options["repository_root"]),
                skill_root=str(options["skill_root"]),
                inputs=options["inputs"],
                connection=options["connection"].model_dump(mode="json"),
                write_roots=("code.txt", "regression.txt"),
            )
            try:
                child = subprocess.run(
                    [sys.executable, "-c", _CRASHING_CAMPAIGN_CONTROLLER, json.dumps(selected)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=60,
                    check=False,
                )
            finally:
                controller_dead.set()
            assert child.returncode == exit_code, child.stderr.decode()
            database = LocalDatabase(database_path)
            journal = Journal(database)
            view = journal.read_view()
            campaign = view.campaign("crash-campaign")
            details = view.run_details()
            expected_phases = ["review", "adjudicate", "repair"] if cut == "apply" else ["review"]
            assert [phase.context.phase for phase in campaign.phases] == expected_phases
            assert tuple(phase.fleet_run_id for phase in campaign.phases) == tuple(
                detail.run.fleet_run_id for detail in details
            )
            assert campaign.terminal_decision is None
            assert not details[-1].run.status.terminal
            assert not details[-1].operations
            assert all(detail.outcome.value == "SUCCESS" for detail in details[:-1])
            report = project_agent_campaign("crash-campaign", details, campaign=campaign)
            assert report["result"] == "incomplete"
            assert report["controller_result"]["availability"] == "unavailable"
            with pytest.raises(JournalConflict, match="missing or incomplete"):
                journal.agent_campaign_result("crash-campaign")
            with pytest.raises(StateConflict, match="active or unresolved Runs"):
                reset_local_state(database_path)
            assert journal.read_view().revision == view.revision
            from oh_goon.core import process_birth

            # Reaped or inaccessible procfs is not a veto over native pidfd
            # readiness/ESRCH. The crash must still close its actual processes;
            # unknown identity never authorizes signaling a numeric PID.
            with monkeypatch.context() as unavailable_procfs:
                unavailable_procfs.setattr(
                    process_birth, "observe_process_birth", lambda _pid: None
                )
                unavailable_procfs.setattr(
                    process_birth, "observe_process_liveness", lambda _pid: None
                )
                started = _assert_started_crash_processes_exited(details)
            if cut == "process":
                assert len(started) == 1
                assert not any(
                    isinstance(event, DescendantsQuiescentEvent)
                    for event in details[-1].lifecycle_events
                )
                # Host observation does not manufacture the missing owner cleanup receipt.
                assert details[-1].game_tape.status.value != "PASSED"
            if cut == "apply":
                assert len(observed) == 6
                assert (repo / "code.txt").read_text() == "repaired\n"
                assert (repo / "regression.txt").is_file()
                attempts = RepositoryEffectAttemptStore(database).all()
                assert len(attempts) == 1 and attempts[0].status.value == "EFFECT_STARTED"
                assert attempts[0].fleet_run_id == details[-1].run.fleet_run_id
                assert journal.operation(attempts[0].operation_id) is None
                settled_processes = {
                    event.physical_execution_id
                    for event in details[-1].lifecycle_events
                    if isinstance(event, DescendantsQuiescentEvent)
                }
                assert all(
                    event.physical_execution_id in settled_processes
                    for event in started
                    if event.fleet_run_id == details[-1].run.fleet_run_id
                )
            else:
                assert (repo / "code.txt").read_text() == "broken\n"
                assert not (repo / "regression.txt").exists()
            assert (path.parent / "goon.lock").read_bytes() == lock_before
            admitted_ids = tuple(detail.run.fleet_run_id for detail in details)
            model_calls = len(observed)
            assert journal.read_view().revision == view.revision
            assert len(observed) == model_calls  # Canonical readback itself issued no work.
            if cut == "apply":
                # Startup may run the existing recovery owner, never campaign policy.
                # Actual applied external effects must remain quarantined for an
                # explicit original reconciliation/acknowledgement before mutation.
                reopened = ApplicationRuntime.build(standard_local=True)
                try:
                    assert (
                        tuple(
                            detail.run.fleet_run_id for detail in journal.read_view().run_details()
                        )
                        == admitted_ids
                    )
                    assert journal.agent_campaign("crash-campaign").terminal_decision is None
                    assert (repo / "code.txt").read_text() == "repaired\n"
                    assert len(observed) == model_calls
                    probe = tmp_path / "restart-probe"
                    probe.mkdir()
                    probe_plan = probe / "goon.yml"
                    probe_plan.write_text(
                        "execution: {purpose: maintenance}\nsteps:\n- run: touch forbidden-replay\n"
                    )
                    reopened.plans.lock(probe_plan)
                    with pytest.raises((RepositoryEffectQuarantined, RuntimeSafetyBlocked)):
                        reopened.plans.run(
                            probe_plan,
                            repository_ids={repository_id(repo)},
                            fleet_run_id="forbidden-replay",
                        )
                    assert not (repo / "forbidden-replay").exists()
                    assert (
                        tuple(
                            detail.run.fleet_run_id for detail in journal.read_view().run_details()
                        )
                        == admitted_ids
                    )
                finally:
                    reopened.shutdown(wait_for_runs=True)
        finally:
            controller_dead.set()
            server.shutdown()
            worker.join(timeout=5)
        assert not worker.is_alive()
