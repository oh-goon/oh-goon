"""Native admitted Steps, local HTTP model, durable phase Runs and fresh re-review."""

import json
import os
import shutil
import subprocess
import sys
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

import pytest
import yaml

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_lock import run_stateless_lock
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
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import repository_id
from oh_goon.engine.git import GitService
from oh_goon.integrations.agent_native_execution import native_executable_sha256
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import native_profile_command_runner
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from oh_goon.services.execution_lock_persistence import write_execution_lock
from tests.factories.agent_review import agent_review_case


@pytest.fixture
def installed_cli(monkeypatch):
    cli = Path(sys.executable).with_name("oh-goon")
    assert cli.is_file(), "native locked environment must provide the apply CLI"
    monkeypatch.setenv("PATH", str(cli.parent) + os.pathsep + os.environ.get("PATH", ""))


@pytest.mark.parametrize("profile_flags", [[], ["--agent-model-profile", "review"]])
def test_single_review_uses_normal_lock_and_run_without_loop_limits(
    tmp_path, monkeypatch, capsys, profile_flags
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
            selection = [
                "--input",
                "repo=local:repo",
                "--agent-repository-root",
                str(options["repository_root"]),
                "--agent-skill-root",
                str(tmp_path),
                "--allow-agent-acceptance",
                "agentic-review",
                "--agent-providers-file",
                str(providers),
                *profile_flags,
            ]
            parser = build_parser()
            assert run_stateless_lock(parser.parse_args(["lock", str(path), *selection])) == 0
            capsys.readouterr()
            runtime = ApplicationRuntime.build(standard_local=True)
            try:
                runtime.locations.add(repo.parent)
                args = parser.parse_args(
                    [
                        "plan",
                        "run",
                        str(path),
                        "--repository-id",
                        repository_id(repo),
                        "--standard-local",
                        "--wait",
                        "--json",
                        *selection,
                    ]
                )
                assert run_plan_command(runtime, args) == 0
                receipt = json.loads(capsys.readouterr().out)
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
            plan, deliveries, _ = resolver.resolve_frozen_agents(path, **options)
            assert "fixture-secret-not-a-source" not in plan.model_dump_json()
            assert all(
                b"fixture-secret-not-a-source" not in handoff.encode_prework_delivery()
                for handoff in deliveries.values()
            )
            write_execution_lock(path.parent / "goon.lock", build_execution_lock(plan))
            lock = (path.parent / "goon.lock").read_bytes()
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
                result = runtime.plans.repair(
                    path,
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
                    "\n".join(chunk.text for chunk in output.chunks) if output else str(result)
                )
                phases = [detail.run.target.agent_repair.phase for detail in result.attempts]
                assert phases == expected_phases
                assert len(observed) == calls
                review_steps = [
                    step.step_id
                    for step in plan.steps
                    if getattr(step, "harness", None) == "provider-review"
                ]
                assert [o["reviewer"] for o in observed[: min(calls, 4)]] == review_steps[
                    : min(calls, 4)
                ]
                assert len({o["candidate_identity"] for o in observed[:4]}) == 1
                assert (path.parent / "goon.lock").read_bytes() == lock
                for index, detail in enumerate(result.attempts):
                    context = detail.run.target.agent_repair
                    assert context.max_attempts == 4 * (passes - 1) + 2
                    assert context.previous_run_id == (
                        result.attempts[index - 1].run.fleet_run_id if index else None
                    )
                    assert detail.run.status.terminal
                    assert not detail.rerun_supported
                    assert runtime.runs.get(detail.run.fleet_run_id).operations == detail.operations
                    assert "fixture-secret-not-a-source" not in detail.model_dump_json()
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
                if scenario in ("repair", "remote", "arbitrary", "regression", "large_scope"):
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
            if scenario in ("repair", "large_scope"):
                reopened = ApplicationRuntime.build(standard_local=True)
                try:
                    assert [
                        reopened.runs.get(identifier).run.target.agent_repair.phase
                        for identifier in result.admitted_run_ids
                    ] == expected_phases
                    assert len(observed) == calls
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
            plan, handoffs, _ = resolver.resolve_frozen_agents(path, **options)
            execution_lock = build_execution_lock(plan)
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
                    budget=AgentRepairBudget(
                        max_attempts=2, timeout_seconds=180.0, step_timeout_seconds=30.0
                    ),
                    inputs=options["inputs"],
                    agent_repository_root=options["repository_root"],
                    agent_skill_root=options["skill_root"],
                    allowed_agent_acceptance=options["allowed_acceptance"],
                    review_write_roots=("src", "tests"),
                    native_agent=selected,
                    native_workspace=repo,
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
