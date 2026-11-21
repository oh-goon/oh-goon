"""A failed native regression feeds a fresh, source-bound campaign cycle."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest
import yaml

from oh_goon.application import ApplicationRuntime
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.agent_providers import ResolvedNativeAgent
from oh_goon.core.models.agent_repair import AgentRepairBudget
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import repository_id
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_plan_process_isolation import (
    ProcessIsolationBackend,
    native_profile_command_runner,
)
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from oh_goon.services.execution_lock_persistence import write_execution_lock
from tests.factories.agent_review import agent_review_case

pytestmark = [pytest.mark.process, pytest.mark.no_resume]

_REGRESSION = '#!/bin/sh\n[ "$(cat code.txt)" = repaired ]\n'
_FINAL_REGRESSION = _REGRESSION + "[ \"$(wc -l < code.txt | tr -d ' ')\" = 1 ]\n"


def exercise_regression_recovery(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    false_clean: bool = False,
    native_agent: ResolvedNativeAgent | None = None,
    process_isolation: ProcessIsolationBackend | None = None,
) -> dict[str, Any]:
    """Portable fixture: the unchanged old product stops; the new policy recovers."""
    observed: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    replies: list[dict[str, Any]] = []
    assert (native_agent is None) == (process_isolation is None)
    code_path = "code.txt" if native_agent is None else "src/code.txt"
    regression_path = "regression.txt" if native_agent is None else "tests/regression.txt"
    regression = _REGRESSION.replace("code.txt", code_path)
    final_regression = _FINAL_REGRESSION.replace("code.txt", code_path)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            size = int(self.headers["Content-Length"])
            assert 0 < size <= 512 * 1024 and len(requests) < 16
            request: dict[str, Any] = json.loads(self.rfile.read(size))
            source: dict[str, Any] = json.loads(request["messages"][1]["content"])
            system = request["messages"][0]["content"]
            identity = source["identity"]
            members = {item["path"]: item for item in source["repository"]}
            member = members["repo/" + code_path]
            stage = member["content"].strip()
            requests.append(source)
            observed.append(
                {
                    "identity": identity,
                    "code": stage,
                    "regression_feedback": source["context"].get("regression_feedback"),
                }
            )
            if 'schema="oh-goon.agent-review/v1"' in system:
                clean = stage == "repaired" or (false_clean and stage == "introduced-regression")
                record = dict(
                    schema="oh-goon.agent-review/v1",
                    **identity,
                    reviewed_paths=list(members),
                    verdict="CLEAN" if clean else "FINDING",
                    summary="Current candidate review",
                    findings=[]
                    if clean
                    else [
                        dict(
                            finding_id="original" if stage == "broken" else "introduced",
                            explanation="Original defect"
                            if stage == "broken"
                            else "New regression",
                            evidence=[dict(path=member["path"], sha256=member["sha256"], line=1)],
                        )
                    ],
                )
            elif 'schema="oh-goon.agent-adjudication/v1"' in system:
                findings = [
                    dict(reviewer=review["reviewer"], finding_id=finding["finding_id"])
                    for review in source["context"]["reviews"]
                    for finding in review["findings"]
                ]
                record = dict(
                    schema="oh-goon.agent-adjudication/v1",
                    candidate_identity=identity["candidate_identity"],
                    scope_identity=identity["scope_identity"],
                    dispositions=[
                        {**finding, "decision": "repair", "rationale": "Current scoped defect"}
                        for finding in findings
                    ],
                    queue=[
                        dict(
                            queue_id="current",
                            findings=findings,
                            rationale="One current defect",
                            regression_expectation="Check repaired code",
                        )
                    ],
                )
            else:
                first = stage == "broken"
                test = members.get("repo/" + regression_path)
                record = dict(
                    schema="agent-patch-batch-v1",
                    candidate_identity=identity["candidate_identity"],
                    summary="First repair introduces a regression"
                    if first
                    else "Repair the diagnosed regression",
                    regressions=[
                        dict(
                            queue_id="current",
                            path="repo/" + regression_path,
                            expectation="Code and single-line behavior are verified",
                        )
                    ],
                    operations=[
                        dict(
                            action="update",
                            path="repo/" + code_path,
                            source_sha256=member["sha256"],
                            replacement="introduced-regression\n" if first else "repaired\n",
                        ),
                        dict(
                            action="create" if first else "update",
                            path="repo/" + regression_path,
                            source_sha256=None if test is None else test["sha256"],
                            replacement=regression if first else final_regression,
                        ),
                    ],
                )
            replies.append(record)
            payload = json.dumps(
                {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(record)}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            pass

    root.mkdir(parents=True, exist_ok=True)
    cli = Path(sys.executable).with_name("oh-goon")
    assert cli.is_file()
    monkeypatch.setenv("PATH", str(cli.parent) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("OH_GOON_DB", str(root / "control/runtime.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(root / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(root / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(root / "cache"))
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=partial(server.serve_forever, poll_interval=0.01))
        worker.start()
        runtime: ApplicationRuntime | None = None
        try:
            resolver, path, repo, options = agent_review_case(
                root,
                endpoint=f"http://127.0.0.1:{server.server_port}",
                native_agent=native_agent,
            )
            if native_agent is not None:
                (repo / "src").mkdir()
                (repo / "tests").mkdir()
                (repo / "code.txt").rename(repo / code_path)
            composite = yaml.safe_load(
                GoonTemplateStore().source("agents/agentic-review-repair-loop").content
            )
            composite["goons"] = [composite["goons"][0], *composite["goons"][4:]]
            package = root / "goons/agents/single-review-loop"
            if native_agent is not None:
                for edge in composite["goons"]:
                    edge["from"] = Path(os.path.relpath(root / "native-goons", package)).as_posix()
            package.mkdir(parents=True)
            (package / "goon.yml").write_text(yaml.safe_dump(composite, sort_keys=False))
            (package / "README.md").write_text("# agents/single-review-loop\n")
            path.write_text(
                "execution: {purpose: verification}\ngoons:\n"
                "- import: agents/single-review-loop\n  from: goons\n"
            )
            (repo / "check").write_text(
                "#!/bin/sh\n"
                f"if [ -f {regression_path} ] && /bin/sh {regression_path}; then exit 0; fi\n"
                "printf 'Regression assertion failed: expected repaired, observed '\n"
                f"cat {code_path}\nexit 1\n"
            )
            (repo / "verify").write_text(
                f'#!/bin/sh\nset -eu\n[ "$(cat {code_path})" = repaired ]\n[ -f {regression_path} ]\n'
            )
            (repo / "verify").chmod(0o755)
            options["inputs"]["verification_command"] = "verify"
            subprocess.run(("git", "init", "-q", str(repo)), check=True, timeout=10)
            plan, _, _ = resolver.resolve_frozen_agents(path, **options)
            lock_path = root / "goon.lock"
            write_execution_lock(lock_path, build_execution_lock(plan))
            original_lock = lock_path.read_bytes()
            if native_agent is None:
                runtime = ApplicationRuntime.build(standard_local=True)
            else:
                runner = native_profile_command_runner(
                    repo,
                    native_agent,
                    process_isolation,
                    RepositoryWorkspaceAuthority.pin_repository_subject,
                )
                git = GitService(
                    command_runner=runner,
                    observation_runner=partial(runner, read_only_workspace=True),
                )
                runtime = ApplicationRuntime.build(
                    standard_local=True,
                    standard_process_isolation=process_isolation,
                    git_service=git,
                )
            runtime.locations.add(repo.parent)
            result = runtime.plans.repair(
                path,
                lock_path=lock_path,
                repository_id=repository_id(repo),
                budget=AgentRepairBudget(
                    max_attempts=3, timeout_seconds=120, step_timeout_seconds=20
                ),
                inputs=options["inputs"],
                agent_repository_root=options["repository_root"],
                agent_skill_root=options["skill_root"],
                allowed_agent_acceptance=options["allowed_acceptance"],
                agent_connection=options.get("connection"),
                review_write_roots=("code.txt", "regression.txt")
                if native_agent is None
                else ("src", "tests"),
                native_agent=native_agent,
                native_workspace=repo if native_agent is not None else None,
                repository=repo if native_agent is not None else None,
                fleet_run_id="regression-recovery",
            )
            phases, failed = [], []
            for item in result.attempts:
                assert isinstance(item.run.target, FleetPlanTarget)
                repair = item.run.target.agent_repair
                assert repair is not None
                phases.append(repair.phase)
                if repair.phase == "regression" and item.outcome.value != "SUCCESS":
                    failed.append(item)
            assert len(failed) == 1
            failure = failed[0]
            old_failure = failure.model_dump(mode="json", exclude={"journal_revision"})
            projection = runtime.runs.agent_campaign("regression-recovery")
            assert lock_path.read_bytes() == original_lock
            native_attempts = []
            for ordinal, detail in enumerate(result.attempts):
                assert isinstance(detail.run.target, FleetPlanTarget)
                context = detail.run.target.agent_repair
                assert context is not None and context.attempt == ordinal + 1
                assert context.max_attempts == 10
                assert context.previous_run_id == (
                    result.attempts[ordinal - 1].run.fleet_run_id if ordinal else None
                )
                assert detail.run.status.terminal
                evidence = detail.operations[0].evidence
                assert isinstance(evidence, FleetPlanOperationEvidence)
                for step in evidence.plan.steps:
                    assert step.settlement.cleanup == "VERIFIED"
                    assert len(step.attempts) == 1
                    assert step.attempts[0].supervisor is not None
                    assert step.attempts[0].supervisor.final_process_group_quiescent is True
                    if native_agent is not None:
                        attempt = step.attempts[0]
                        isolation = attempt.standard_isolation
                        assert isolation is not None
                        assert isolation.workspace_read_only
                        assert isolation.filesystem_cleanup == "verified"
                        assert isolation.credential_visibility == "none"
                        assert isolation.host_and_external_ip_networking == "denied"
                        is_agent = step.kind == "agent"
                        assert isolation.broker_socket_mounted is is_agent
                        assert isolation.scoped_write_roots == (
                            ("src", "tests") if context.phase == "repair" and not is_agent else ()
                        )
                        live = runtime.runs.game_tape_live_output(detail.run.fleet_run_id)
                        assert "RAW_NATIVE_RECOVERY_PROTOCOL_CANARY" not in "".join(
                            chunk.text for chunk in live.chunks
                        )
                        native_attempts.append(
                            {
                                "run_id": detail.run.fleet_run_id,
                                "phase": context.phase,
                                "kind": step.kind,
                                "step_id": step.step_id,
                                "physical_execution_id": attempt.physical_execution_id,
                                "tool": attempt.tool.model_dump(mode="json")
                                if attempt.tool is not None
                                else None,
                                "isolation": isolation.model_dump(mode="json"),
                                "supervisor": step.attempts[0].supervisor.model_dump(mode="json"),
                            }
                        )
            assert failure.outcome.value == "ATTENTION_REQUIRED"
            runtime.shutdown(wait_for_runs=True)
            runtime = ApplicationRuntime.build(standard_local=True)
            reopened = runtime.runs.agent_campaign("regression-recovery")
            assert reopened["result"] == projection["result"]
            assert reopened["runs"] == projection["runs"]
            assert (
                runtime.runs.get(failure.run.fleet_run_id).model_dump(
                    mode="json", exclude={"journal_revision"}
                )
                == old_failure
            )
            with (
                (root / "fresh-readback.json").open("w+b") as output,
                tempfile.TemporaryFile() as errors,
            ):
                subprocess.run(
                    (str(cli), "runs", "handoff", result.admitted_run_ids[-1], "--json"),
                    stdout=output,
                    stderr=errors,
                    timeout=30,
                    check=True,
                )
                errors.seek(0)
                assert len(errors.read(65537)) <= 65536
                output.seek(0)
                body = output.read(1024 * 1024 + 1)
            assert len(body) <= 1024 * 1024
            readback = json.loads(body)
            assert readback["fleet_run_id"] == result.admitted_run_ids[-1]
            canonical_campaign = runtime.runs.campaign_journal(result.repair_id).model_dump(
                mode="json"
            )
            assert readback["campaign_journal"]["campaign_id"] == canonical_campaign["campaign_id"]
            assert (
                readback["campaign_journal"]["complete"] is canonical_campaign["complete"] is True
            )
            assert readback["campaign_journal"]["records"] == canonical_campaign["records"]
            failure_evidence = failure.operations[0].evidence
            assert isinstance(failure_evidence, FleetPlanOperationEvidence)
            report = {
                "stop_reason": result.stop_reason.value,
                "phases": phases,
                "run_ids": list(result.admitted_run_ids),
                "observed": observed,
                "requests": requests,
                "replies": replies,
                "failed_run": failure.run.fleet_run_id,
                "failed_step": failure_evidence.plan.steps[0].model_dump(mode="json"),
                "projection": reopened,
                "candidate": (repo / code_path).read_text(),
                "fresh_process_readback": True,
                "fresh_process_campaign_agreement": True,
                **({"native_attempts": native_attempts} if native_agent is not None else {}),
            }
            (root / "recovery-result.json").write_text(json.dumps(report, indent=2) + "\n")
            return report
        finally:
            original_error = sys.exception()
            cleanup_error: BaseException | None = None
            try:
                if runtime is not None:
                    runtime.shutdown(wait_for_runs=True)
            except BaseException as error:
                cleanup_error = error
            try:
                server.shutdown()
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error = error
                else:
                    cleanup_error.add_note(
                        f"HTTP server shutdown also failed: {type(error).__name__}"
                    )
            finally:
                try:
                    worker.join(timeout=5)
                    assert not worker.is_alive()
                except BaseException as error:
                    if cleanup_error is None:
                        cleanup_error = error
                    else:
                        cleanup_error.add_note(
                            f"HTTP thread retirement also failed: {type(error).__name__}"
                        )
            if cleanup_error is not None:
                if original_error is None:
                    raise cleanup_error
                original_error.add_note(
                    f"Fixture cleanup also failed: {type(cleanup_error).__name__}"
                )


def test_failed_regression_recovers_with_fresh_candidate_and_preserved_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = exercise_regression_recovery(tmp_path, monkeypatch)
    assert report["stop_reason"] == "converged", report
    assert report["phases"] == [
        "review",
        "adjudicate",
        "repair",
        "regression",
        "review",
        "adjudicate",
        "repair",
        "regression",
        "review",
        "verification",
    ]
    assert report["candidate"] == "repaired\n"
    assert report["failed_step"]["status"] == "FAILED"
    assert report["projection"]["result"] == "converged"
    second = [item for item in report["observed"] if item["code"] == "introduced-regression"]
    assert len(second) == 3
    for observation in second:
        feedback = observation["regression_feedback"]
        assert feedback["fleet_run_id"] == report["failed_run"]
        assert "Regression assertion failed" in feedback["feedback"][0]["text"]
        assert feedback["candidate_identity"] == observation["identity"]["candidate_identity"]


def test_clean_review_cannot_override_known_failed_regression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = exercise_regression_recovery(tmp_path, monkeypatch, false_clean=True)
    assert report["stop_reason"] == "regression_failed", report
    assert report["phases"] == ["review", "adjudicate", "repair", "regression", "review"]
    assert report["candidate"] == "introduced-regression\n"
    assert report["projection"]["result"] != "converged"
