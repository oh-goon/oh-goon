"""Selected native two-Run qualification; serial product defaults stay unchanged."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
from pathlib import Path
from threading import Event

import pytest

from oh_goon.core.errors import ExecutionBusy, FleetPlanInvalid
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.parallel_agent_work import ParallelAgentWorkRequest
from oh_goon.core.models.parallel_candidate_verification import (
    FrozenVerifierInput,
    ParallelCandidateVerifier,
)
from oh_goon.core.models.run_resources import RunConcurrencyPolicy, RunResourceEnvelopeEvidence
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.process_birth import observe_process_birth
from oh_goon.core.vocabulary import FleetRunStatus
from oh_goon.engine.git import GitService
from oh_goon.integrations import run_resource_envelope as native_resources
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.parallel_agent_work import cancel_parallel_work
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from tests.factories.native_run_concurrency import selected_native_concurrency_environment
from tests.factories.native_two_run import NativeTwoRunCase, native_two_run_case

_POLICY_ENV = "OH_GOON_HUG223_SELECTED_POLICY"
_PARALLEL_WORK_ID = "same-repository-native-proof"


def _start_same_repository_pair(
    case: NativeTwoRunCase, policy: RunConcurrencyPolicy
) -> dict[str, str]:
    repository = case.repositories["a"]
    git = GitService()
    base = git.observe(repository).head_sha
    assert base is not None
    executable = str(Path("/usr/bin/python3").resolve(strict=True))
    script = (
        "import pathlib,sys\n"
        "candidate=pathlib.Path(sys.argv[1])\n"
        "assert candidate.joinpath('code.py').read_bytes() in (b'VALUE = 42\\n', b'VALUE = 43\\n')\n"
    )
    request = ParallelAgentWorkRequest(
        work_id=_PARALLEL_WORK_ID,
        workspace=repository,
        expected_base_revision=base,
        task="Edit one worktree candidate",
        briefs=("Set VALUE to 42", "Set VALUE to 43"),
        native_profiles=(case.selected["a"].profile_name, case.selected["b"].profile_name),
        capacity=policy,
        notes_enabled=False,
        verifier=ParallelCandidateVerifier(
            acceptance="The candidate establishes either accepted VALUE alternative",
            executable=executable,
            executable_sha256=hashlib.sha256(Path(executable).read_bytes()).hexdigest(),
            arguments=("-I", "{input:verify.py}", "{candidate}"),
            inputs=(
                FrozenVerifierInput(
                    name="verify.py",
                    content=script,
                    sha256=hashlib.sha256(script.encode()).hexdigest(),
                ),
            ),
        ),
    )
    readback = case.runtime.plans.start_parallel_agent_work(
        request, selected=(case.selected["a"], case.selected["b"])
    )
    assert readback.dispatch_closed and all(
        child.dispatch == "admitted" for child in readback.children
    ), readback.model_dump_json()
    fleet_run_ids = dict(
        zip(("a", "b"), (child.fleet_run_id for child in readback.children), strict=True)
    )
    workspaces = RepositoryWorkspaceAuthority(LocalDatabase(case.database_path))
    roots = {}
    for label, run_id in fleet_run_ids.items():
        target = case.runtime.runs.get(run_id).run.target
        assert isinstance(target, FleetPlanTarget) and target.candidate_workspace is not None
        assert target.candidate_workspace.workspace.base_revision == base
        roots[label] = workspaces.physical_path(target.candidate_workspace.workspace)
    assert roots["a"] != roots["b"] != repository and roots["a"] != repository
    for label, peer in (("a", "b"), ("b", "a")):
        case.forbidden_paths[label] = (
            repository / "code.py",
            roots[peer] / "code.py",
            repository / ".git/HEAD",
        )
    return fleet_run_ids


@pytest.mark.certification
@pytest.mark.no_resume
@pytest.mark.parametrize("failure", ["none", "cancel-a", "worker-crash-a"])
@pytest.mark.parametrize(
    "same_repository", [False, True], ids=["distinct-repositories", "same-repository"]
)
def test_two_real_native_runs_share_one_runtime_and_refuse_third(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    same_repository: bool,
) -> None:
    if not os.environ.get("OH_GOON_NATIVE_PI") or not os.environ.get(
        "OH_GOON_NATIVE_PI_RUNTIME_ROOT"
    ):
        pytest.fail(
            "Select the public installed Pi executable and complete runtime root explicitly"
        )
    selected = os.environ.get(_POLICY_ENV)
    if selected is None:
        with selected_native_concurrency_environment(
            aggregate_resources=CicdResourcePolicy(
                cpu_millis=2000, memory_mib=3072, max_processes=1024
            ),
            worker_resources=CicdResourcePolicy(cpu_millis=750, memory_mib=768, max_processes=256),
            controller_reserve=CicdResourcePolicy(
                cpu_millis=500, memory_mib=1024, max_processes=256
            ),
        ) as environment:
            child_environment = dict(os.environ)
            child_environment[_POLICY_ENV] = environment.policy.model_dump_json()
            result = environment.run(
                (
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-s",
                    "-m",
                    "certification",
                    str(Path(__file__)) + "::" + request.node.name,
                ),
                cwd=Path(__file__).parents[3],
                environment=child_environment,
                timeout=240,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            print(result.stdout)
        return
    policy = RunConcurrencyPolicy.model_validate_json(selected)
    program = Path(os.environ["OH_GOON_NATIVE_PI"]).resolve(strict=True)
    public_runtime = Path(os.environ["OH_GOON_NATIVE_PI_RUNTIME_ROOT"]).resolve(strict=True)
    with native_two_run_case(
        tmp_path,
        policy,
        program=program,
        public_runtime=public_runtime,
        probe_namespaces=True,
        same_repository=same_repository,
    ) as case:
        if same_repository:
            git = GitService()
            repository = case.repositories["a"]
            original_git = git.observe(repository).fingerprint.state_id
            original_refs = git.run("show-ref", "--head", cwd=repository)
            assert original_refs.returncode == 0
            run_ids = _start_same_repository_pair(case, policy)
            a = case.runtime.runs.get(run_ids["a"]).run
            b = case.runtime.runs.get(run_ids["b"]).run
        else:
            a = case.start("a")
            b = None
        assert case.entered["a"].wait(45), case.runtime.runs.game_tape_live_output(
            a.fleet_run_id
        ).model_dump_json()
        if b is None:
            b = case.start("b")
        assert case.entered["b"].wait(45), case.runtime.runs.game_tape_live_output(
            b.fleet_run_id
        ).model_dump_json()
        print("HUG223_BOTH_NATIVE_READY", flush=True)
        active = case.active_reservations({"a": a.fleet_run_id, "b": b.fleet_run_id})
        envelopes = []
        for value in active.values():
            assert value.envelope is not None
            assert value.lease.custody is not None and value.lease.custody.phase == "WORKER_OWNED"
            envelopes.append(value.envelope)
        assert len({value.process_birth.pid for value in envelopes}) == 2
        assert os.getpid() not in {value.process_birth.pid for value in envelopes}
        assert len({value.unit_name for value in envelopes}) == 2
        assert len({value.invocation_id for value in envelopes}) == 2
        assert len({value.admission.budget_identity for value in active.values()}) == 1
        with pytest.raises(ExecutionBusy):
            case.start("third")
        for event in case.release.values():
            event.set()
        assert case.written["a"].wait(45) and case.written["b"].wait(45), case.errors
        allow_a_finalization = Event()
        a_finalization_attempted = Event()
        original_finisher = native_resources.finish_worker_resource_scope

        def hold_original_a_finalizer(
            deferred: native_resources.DeferredWorkerResourceScope,
            envelope: RunResourceEnvelopeEvidence | None,
            *,
            pinned: native_resources.PinnedRunResourceScope | None,
        ) -> native_resources.VerifiedWorkerResourceClosure:
            if (
                deferred.admission.fleet_run_id == a.fleet_run_id
                and not allow_a_finalization.is_set()
            ):
                a_finalization_attempted.set()
                raise FleetPlanInvalid("Selected native closure is temporarily unavailable")
            return original_finisher(deferred, envelope, pinned=pinned)

        try:
            if failure == "cancel-a":
                if same_repository:
                    readback = cancel_parallel_work(
                        LocalDatabase(case.database_path),
                        _PARALLEL_WORK_ID,
                        fleet_run_id=a.fleet_run_id,
                    )
                    assert readback.children[0].status is FleetRunStatus.CANCELLING
                    assert readback.children[1].status is FleetRunStatus.RUNNING
                else:
                    case.runtime.runs.cancel(a.fleet_run_id)
                cancelled = case.runtime.runs.wait_for_terminal(a.fleet_run_id, timeout=60)
                assert cancelled.run.status.value == "CANCELLED"
            elif failure == "worker-crash-a":
                monkeypatch.setattr(
                    native_resources, "finish_worker_resource_scope", hold_original_a_finalizer
                )
                a_envelope = active["a"].envelope
                assert a_envelope is not None
                worker_birth = a_envelope.process_birth
                assert observe_process_birth(worker_birth.pid) == worker_birth
                os.kill(worker_birth.pid, signal.SIGKILL)
                assert a_finalization_attempted.wait(45)
                assert not case.runtime.runs.get(a.fleet_run_id).run.status.terminal
                if same_repository:
                    readback = cancel_parallel_work(
                        LocalDatabase(case.database_path),
                        _PARALLEL_WORK_ID,
                        fleet_run_id=a.fleet_run_id,
                    )
                    assert readback.children[0].cancellation_requested
                    assert readback.children[0].status is FleetRunStatus.RUNNING
                    assert readback.children[1].status is FleetRunStatus.RUNNING
            else:
                case.finish["a"].set()
            b_before = active["b"].envelope
            assert b_before is not None
            assert observe_process_birth(b_before.process_birth.pid) == b_before.process_birth
            case.finish["b"].set()
            b_result = case.runtime.runs.wait_for_terminal(b.fleet_run_id, timeout=90)
            assert b_result.outcome.value == "SUCCESS", b_result.model_dump_json()
            if failure == "worker-crash-a":
                # B has completed its later verifier/effect guards while A's
                # original reservation and orphaned lifecycle remain unresolved.
                assert not case.runtime.runs.get(a.fleet_run_id).run.status.terminal
                database = LocalDatabase(case.database_path, read_only=True)
                with database.transaction() as connection:
                    assert (
                        ExecutionCoordinator(database).worker_resource_reservation_in(
                            connection, a.fleet_run_id
                        )
                        is not None
                    )
            allow_a_finalization.set()
            case.release_all()
            if failure == "worker-crash-a":
                # The original captured callback, not a fresh Boolean receipt,
                # retries native closure and original scoped recovery.
                case.runtime.shutdown(wait_for_runs=False)
                a_result = case.runtime.runs.get(a.fleet_run_id)
                assert a_result.run.status.terminal
                assert a_result.outcome.value != "SUCCESS"
            else:
                a_result = case.runtime.runs.wait_for_terminal(a.fleet_run_id, timeout=60)
                assert a_result.outcome.value == ("SUCCESS" if failure == "none" else "CANCELLED")
            results = [a_result, b_result]
        finally:
            allow_a_finalization.set()
            case.release_all()
        assert not case.errors, case.errors
        assert all(
            result.resource_observation is not None
            and result.resource_observation.cleanup == "VERIFIED"
            for result in results
        )
        for label, run in (("a", a), ("b", b)):
            assert (case.repositories[label] / "code.py").read_text().endswith("VALUE = 41\n")
            assert case.facts[label]["owner"] == label
            inspection = case.runtime.plans.inspect_candidate_run(run.fleet_run_id)
            assert inspection.path is not None
            expected = b"VALUE = 43\n" if same_repository and label == "b" else b"VALUE = 42\n"
            assert (Path(inspection.path) / "code.py").read_bytes() == expected
        if same_repository:
            for detail in results:
                if detail.outcome.value == "SUCCESS":
                    evidence = detail.operations[0].evidence
                    assert isinstance(evidence, FleetPlanOperationEvidence)
                    receipt = evidence.candidate_receipt
                    assert receipt is not None and receipt.checkpoint is not None
                    verifier = evidence.plan.steps[-1].attempts[0].parallel_verifier
                    assert verifier is not None and verifier.postcondition == "UNCHANGED"
                    assert verifier.source_sha256 == receipt.checkpoint.source_sha256
                    verified = case.runtime.plans.verify_parallel_candidate(detail.run.fleet_run_id)
                    assert verified.outcome == "PASS", verified.model_dump_json()
                    assert verified.checkpoint_id == receipt.checkpoint.checkpoint_id
                    assert verified.source_sha256 == receipt.checkpoint.source_sha256
                    if failure == "none" and detail.run.fleet_run_id == b.fleet_run_id:
                        destination = tmp_path / "selected-candidate.patch"
                        assert not destination.exists()
                        exported = case.runtime.plans.export_parallel_candidate(
                            detail.run.fleet_run_id,
                            checkpoint_id=receipt.checkpoint.checkpoint_id,
                            destination=destination,
                        )
                        patch = destination.read_bytes()
                        assert exported.verification == verified
                        assert exported.destination == str(destination)
                        assert exported.cleanup == "VERIFIED"
                        assert exported.changed_paths == ("code.py",)
                        assert exported.byte_count == len(patch)
                        assert exported.sha256 == hashlib.sha256(patch).hexdigest()
                        assert b"diff --git a/code.py b/code.py" in patch
                        assert b"-VALUE = 41\n+VALUE = 43\n" in patch
            assert git.observe(repository).fingerprint.state_id == original_git
            observed_refs = git.run("show-ref", "--head", cwd=repository)
            assert observed_refs.returncode == 0 and observed_refs.stdout == original_refs.stdout
        # Guest paths may deliberately match. Both real tools atomically created
        # the same filename in HOME and TMP before either namespace was retired.
        # Shared directories cannot admit both O_EXCL creations, even in a race.
        for area in ("home", "tmp", "mount_namespace"):
            assert case.facts["a"]["namespaces"][area] != case.facts["b"]["namespaces"][area]
        print(
            json.dumps(
                {
                    "two_original_runs": "PASS",
                    "failure": failure,
                    "run_ids": [r.run.fleet_run_id for r in results],
                    "worker_pids": [envelope.process_birth.pid for envelope in envelopes],
                    "candidate_roots": [case.facts[label]["cwd"] for label in ("a", "b")],
                    "homes": [case.facts[label]["home"] for label in ("a", "b")],
                    "temps": [case.facts[label]["tmp"] for label in ("a", "b")],
                    "namespaces": [case.facts[label]["namespaces"] for label in ("a", "b")],
                }
            ),
            flush=True,
        )
