"""The public parallel CLI admits original Pi Runs and reopens their evidence."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_ai import resolve_native_cli_selection
from oh_goon.core.errors import StateConflict
from oh_goon.core.models.agent_providers import AgentProvidersConfig, PiSessionIntent
from oh_goon.core.models.branch_advance import BranchAdvanceReceipt, PreparedBranchAdvance
from oh_goon.core.models.fleet_plan import ResolvedAgentStep, ResolvedNativeAgentWorkload
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.parallel_agent_work import ParallelAgentWorkRequest
from oh_goon.core.models.parallel_candidate_verification import (
    FrozenVerifierInput,
    ParallelCandidateVerifier,
)
from oh_goon.core.models.run_resources import RunConcurrencyPolicy
from oh_goon.core.models.schedule import ScheduleOccurrence, ScheduleOccurrenceRepositoryRun
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import (
    FleetRunOutcome,
    FleetRunSource,
    ScheduleExecutionStatus,
    ScheduleRepositoryRunStatus,
)
from oh_goon.engine.command import run_command
from oh_goon.engine.git import GitService
from oh_goon.runtime.agent_notes import AgentNotesStore
from oh_goon.runtime.branch_advances import BranchAdvanceStore
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from oh_goon.services import schedule_timing
from oh_goon.services.branch_advance import BranchAdvanceRequest, advance_verified_candidate
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from tests.factories.native_run_concurrency import selected_native_concurrency_environment
from tests.factories.native_two_run import (
    PAIR_VERIFICATION_ARGV,
    NativeTwoRunProvider,
    native_two_run_provider,
)

_POLICY_ENV = "OH_GOON_NATIVE_PARALLEL_CLI_POLICY"
_HOST_ENV = (
    "PATH",
    "LANG",
    "LC_ALL",
    "DBUS_SESSION_BUS_ADDRESS",
    "XDG_RUNTIME_DIR",
    "UV_OFFLINE",
    "GIT_ALLOW_PROTOCOL",
)
_STATE_ENV = (
    "OH_GOON_DB",
    "OH_GOON_LEAGUE_FILE",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "HASHMARKS_NO_UPDATE_CHECK",
)


def _environment(names: tuple[str, ...]) -> dict[str, str]:
    return {name: os.environ[name] for name in names if name in os.environ}


def _selected_pi_environment(
    request: pytest.FixtureRequest, *, timeout: int
) -> tuple[RunConcurrencyPolicy, Path, Path] | None:
    native_pi = os.environ.get("OH_GOON_NATIVE_PI")
    runtime_root = os.environ.get("OH_GOON_NATIVE_PI_RUNTIME_ROOT")
    assert native_pi and runtime_root, "Select the installed Pi executable and runtime root"
    selected = os.environ.get(_POLICY_ENV)
    if selected is not None:
        return (
            RunConcurrencyPolicy.model_validate_json(selected),
            Path(native_pi).resolve(strict=True),
            Path(runtime_root).resolve(strict=True),
        )
    with selected_native_concurrency_environment(
        aggregate_resources=CicdResourcePolicy(
            cpu_millis=2000, memory_mib=3072, max_processes=1024
        ),
        worker_resources=CicdResourcePolicy(cpu_millis=750, memory_mib=768, max_processes=256),
        controller_reserve=CicdResourcePolicy(cpu_millis=500, memory_mib=1024, max_processes=256),
    ) as environment:
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
            environment={
                **_environment(_HOST_ENV),
                _POLICY_ENV: environment.policy.model_dump_json(),
                "OH_GOON_NATIVE_PI": native_pi,
                "OH_GOON_NATIVE_PI_RUNTIME_ROOT": runtime_root,
            },
            timeout=timeout,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    return None


def _write_catalog(path: Path, case: NativeTwoRunProvider) -> None:
    first = case.selected["a"]
    models = {}
    for selected in case.selected.values():
        assert selected.connection is not None
        models[selected.connection.profile] = selected.connection.selection
    assert first.connection is not None
    config = AgentProvidersConfig(
        providers={first.connection.provider_name: first.connection.provider},
        models=models,
        native_agents={"pi": first.definition},
        native_profiles={
            selected.profile_name: selected.profile for selected in case.selected.values()
        },
    )
    path.write_text(config.model_dump_json(), encoding="utf-8")


def _operator(
    executable: Path,
    repository: Path,
    *argv: str,
    environment: Mapping[str, str] | None = None,
    timeout: int = 140,
) -> tuple[int, bytes, bytes]:
    # Public operator commands need the exact test-owned state coordinates.
    # The native workload command runner intentionally removes those names.
    with (
        tempfile.TemporaryFile(dir=repository.parent) as output,
        tempfile.TemporaryFile(dir=repository.parent) as errors,
    ):
        result = subprocess.run(
            (str(executable), *argv),
            cwd=repository,
            env=dict(environment)
            if environment is not None
            else _environment(_HOST_ENV + _STATE_ENV),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=errors,
            timeout=timeout,
            check=False,
        )
        output.seek(0)
        errors.seek(0)
        stdout = output.read(1024 * 1024 + 1)
        assert len(stdout) <= 1024 * 1024
        return result.returncode, stdout, errors.read(4096)


def _cli_json(
    executable: Path,
    repository: Path,
    *argv: str,
    expected_status: int = 0,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    code, stdout, stderr = _operator(
        executable, repository, *argv, "--json", environment=environment
    )
    assert code == expected_status, (argv, stdout[-4096:], stderr)
    payload = json.loads(stdout)
    assert isinstance(payload, dict)
    payload.pop("_cli", None)
    return payload


def _run_events(cli: Path, repository: Path, run_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    after = 0
    for _ in range(6):
        page = _cli_json(cli, repository, "runs", "events", run_id, "--after", str(after))
        events.extend(page["events"])
        if not page["has_more"]:
            return events
        assert page["next_sequence"] > after
        after = page["next_sequence"]
    pytest.fail("Installed fixture exceeded its bounded Run event readback")


def _commit_verified_patch_on_operator_branch(
    git: GitService,
    repository: Path,
    patch: Path,
    *,
    base_revision: str,
    verified_file: Path,
) -> Path:
    operator = patch.parent / "operator-clone"
    cloned = git.run(
        "clone", "--local", "--no-hardlinks", str(repository), str(operator), cwd=patch.parent
    )
    assert cloned.returncode == 0, cloned.stderr
    base = git.observe(operator)
    assert base.head_sha == base_revision and not base.changed_files
    branch = "qualification/operator-selection"
    for arguments in (
        ("switch", "-c", branch),
        ("apply", "--check", "--index", str(patch)),
        ("apply", "--index", str(patch)),
        (
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=Qualification",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--no-gpg-sign",
            "-qm",
            "Apply the independently verified candidate",
        ),
    ):
        result = git.run(*arguments, cwd=operator)
        assert result.returncode == 0, (arguments, result.stderr)
    committed = git.observe(operator)
    assert committed.fingerprint.branch == branch and not committed.changed_files
    parent = git.run("rev-parse", "HEAD^", cwd=operator)
    assert parent.returncode == 0 and parent.stdout.strip() == base_revision
    committed_blob = git.run("rev-parse", f"{branch}:code.py", cwd=operator)
    verified_blob = git.run("hash-object", "--no-filters", str(verified_file), cwd=operator)
    assert committed_blob.returncode == verified_blob.returncode == 0
    assert committed_blob.stdout == verified_blob.stdout
    assert (operator / "code.py").read_bytes() == verified_file.read_bytes()
    return operator


@pytest.mark.certification
@pytest.mark.no_resume
def test_public_parallel_cli_reopens_verified_candidate_and_replays_exact_request(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected_pi_environment(request, timeout=240)
    if selected is None:
        return

    policy, program, runtime_root = selected
    cli = Path(sys.executable).with_name("oh-goon")
    assert cli.is_file(), "Prepare the selected Oh-Goon CLI environment before qualification"
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(tmp_path / "league.yml"))
    monkeypatch.setenv("HASHMARKS_NO_UPDATE_CHECK", "1")
    with native_two_run_provider(
        tmp_path,
        policy,
        program=program,
        public_runtime=runtime_root,
        same_repository=True,
    ) as case:
        repository = case.repositories["a"]
        git = GitService()
        original = git.observe(repository)
        assert original.head_sha is not None and not original.changed_files
        subject_bytes = (repository / "code.py").read_bytes()
        refs = git.run("show-ref", "--head", cwd=repository)
        assert refs.returncode == 0
        for label in ("a", "b"):
            case.forbidden_paths[label] = (repository / "code.py", repository / ".git/HEAD")
        case.release_all()

        catalog = tmp_path / "providers.json"
        _write_catalog(catalog, case)
        verifier = (
            "from pathlib import Path\n"
            "import sys\n"
            "assert Path(sys.argv[1], 'code.py').read_bytes() == b'VALUE = 42\\n'\n"
        )
        executable = Path("/usr/bin/python3").resolve(strict=True)
        work = ParallelAgentWorkRequest(
            work_id="public-parallel-proof",
            workspace=repository,
            expected_base_revision=original.head_sha,
            task="Compare independent values under a single frozen acceptance check.",
            briefs=("Set VALUE to 42", "Set VALUE to 43"),
            native_profiles=(case.selected["a"].profile_name, case.selected["b"].profile_name),
            provider_catalog=catalog,
            capacity=policy,
            notes_enabled=False,
            verifier=ParallelCandidateVerifier(
                acceptance="Only the candidate containing VALUE = 42 passes.",
                executable=str(executable),
                executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
                arguments=("-I", "{input:verify.py}", "{candidate}"),
                inputs=(
                    FrozenVerifierInput(
                        name="verify.py",
                        content=verifier,
                        sha256=hashlib.sha256(verifier.encode()).hexdigest(),
                    ),
                ),
            ),
        )
        request_file = tmp_path / "request.json"
        request_file.write_text(work.model_dump_json(), encoding="utf-8")
        code, stdout, stderr = _operator(
            cli,
            repository,
            "league",
            "add-folder",
            str(repository),
            timeout=30,
        )
        assert code == 0, (stdout, stderr)
        assert case.database_path.is_file()
        completed = _cli_json(
            cli, repository, "runs", "start-parallel", str(request_file), expected_status=1
        )
        assert completed["dispatch_closed"] and not completed["cancelled"]
        left, right = completed["children"]
        run_a, run_b = [child["fleet_run_id"] for child in (left, right)]
        assert (run_a, run_b) == (f"{work.work_id}-1", f"{work.work_id}-2")
        assert [child["dispatch"] for child in (left, right)] == ["admitted", "admitted"]
        assert [child["outcome"] for child in (left, right)] == [
            "SUCCESS",
            "ATTENTION_REQUIRED",
        ]
        assert completed["intent"]["base_revision"] == original.head_sha
        assert _cli_json(cli, repository, "runs", "parallel-work", work.work_id) == completed

        first = _cli_json(cli, repository, "runs", "candidate", run_a)
        second = _cli_json(cli, repository, "runs", "candidate", run_b)
        candidate_paths = [Path(item["path"]) for item in (first, second)]
        assert len({repository, *candidate_paths}) == 3
        assert first["admission"]["source"] == second["admission"]["source"]
        assert first["admission"]["source_workspace"] != second["admission"]["source_workspace"]
        assert [(path / "code.py").read_bytes() for path in candidate_paths] == [
            b"VALUE = 42\n",
            b"VALUE = 43\n",
        ]
        verdict = _cli_json(cli, repository, "runs", "verify-candidate", run_a)
        assert verdict["outcome"] == "PASS"
        assert verdict["checkpoint_id"] == first["checkpoint"]["checkpoint_id"]
        failed = _cli_json(cli, repository, "runs", "verify-candidate", run_b, expected_status=1)
        assert failed["outcome"] == "FAIL"

        destination = tmp_path / "verified.patch"
        exported = _cli_json(
            cli,
            repository,
            "runs",
            "export-candidate",
            run_a,
            "--checkpoint-id",
            verdict["checkpoint_id"],
            "--output",
            str(destination),
        )
        payload = destination.read_bytes()
        assert exported["verification"] == verdict
        assert exported["changed_paths"] == ["code.py"]
        assert exported["sha256"] == hashlib.sha256(payload).hexdigest()
        assert exported["byte_count"] == len(payload) and exported["cleanup"] == "VERIFIED"
        assert b"+VALUE = 42\n" in payload
        operator = _commit_verified_patch_on_operator_branch(
            git,
            repository,
            destination,
            base_revision=original.head_sha,
            verified_file=candidate_paths[0] / "code.py",
        )
        verifier_file = tmp_path / "verify-operator.py"
        verifier_file.write_text(verifier, encoding="utf-8")
        verified_branch = run_command(
            str(executable), "-I", str(verifier_file), str(operator), cwd=operator, timeout=10
        )
        assert verified_branch.returncode == 0, verified_branch.stderr
        branch = git.observe(operator)
        assert branch.head_sha is not None and branch.fingerprint.branch is not None
        candidate_database = tmp_path / "branch-state" / "application.sqlite"
        branch_environment = {
            **_environment(_HOST_ENV + _STATE_ENV),
            "OH_GOON_DB": str(candidate_database),
            "OH_GOON_LEAGUE_FILE": str(tmp_path / "branch-league.yml"),
        }
        case.forbidden_paths["third"] = (operator / "code.py", operator / ".git/HEAD")
        follow_up = _cli_json(
            cli,
            operator,
            "ai",
            "--agent",
            "pi",
            "--profile",
            case.selected["third"].profile_name,
            "--workspace",
            str(operator),
            "--config",
            str(catalog),
            "--candidate",
            "--expect-branch",
            branch.fingerprint.branch,
            "--expect-head",
            branch.head_sha,
            "--prompt",
            "Confirm this independently verified branch in a fresh candidate.",
            "--verify",
            shlex.join(PAIR_VERIFICATION_ARGV),
            environment=branch_environment,
        )
        assert candidate_database.is_file() and candidate_database != case.database_path
        assert follow_up["outcome"] == "SUCCESS"
        branch_run = follow_up["run"]
        assert branch_run["fleet_run_id"] not in {run_a, run_b}
        assert (
            branch_run["target"]["candidate_workspace"]["workspace"]["base_revision"]
            == branch.head_sha
        )
        assert git.observe(operator).fingerprint.state_id == branch.fingerprint.state_id
        assert git.observe(repository).fingerprint.state_id == original.fingerprint.state_id
        assert (repository / "code.py").read_bytes() == subject_bytes
        assert git.run("show-ref", "--head", cwd=repository).stdout == refs.stdout
        assert not case.errors, case.errors

        request_counts = {key: len(value) for key, value in case.requests.items()}
        changed_request = tmp_path / "changed-request.json"
        changed_request.write_text(
            work.model_copy(update={"task": "Change the original request"}).model_dump_json(),
            encoding="utf-8",
        )
        changed = _cli_json(
            cli, repository, "runs", "start-parallel", str(changed_request), expected_status=2
        )
        assert changed["status"] == "rejected"
        assert changed["code"] == "PLAN_RESOLUTION_REJECTED"
        assert changed["execution"] == "not_started_by_diagnostic"
        assert _cli_json(cli, repository, "runs", "parallel-work", work.work_id) == completed

        # Exact replay is readback even after current source and provider selection change.
        assert (
            git.run(
                "-c",
                "user.name=Qualification",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "--allow-empty",
                "-qm",
                "advance after original work",
                cwd=repository,
            ).returncode
            == 0
        )
        catalog.unlink()
        assert (
            _cli_json(
                cli, repository, "runs", "start-parallel", str(request_file), expected_status=1
            )
            == completed
        )
        assert request_counts == {key: len(value) for key, value in case.requests.items()}


_ORIGINAL_WRITABLE_SOURCE = b"# Original repository a\nVALUE = 41\n"
_ACCEPTED_WRITABLE_SEED = b"VALUE = 42\n"
_FIRST_WRITABLE_EDIT = b"VALUE = 42\n# First verified round\n"
_SECOND_WRITABLE_EDIT = b"VALUE = 42\n# Second verified round\n"


@dataclass(frozen=True)
class _TransferredBranch:
    bundle: Path
    ref: str
    commit: str
    tree: str


@dataclass(frozen=True)
class _WritableSeed:
    cli: Path
    repository: Path
    original: str
    original_refs: str
    bare: Path
    checkout: Path
    ref: str
    catalog: Path
    provider: NativeTwoRunProvider
    accepted: dict[str, Any]
    failed: dict[str, Any]
    calls: dict[str, int]

    @staticmethod
    def native(path: Path, *arguments: str) -> str:
        result = GitService().run(
            "-c", "protocol.allow=never", "-c", "protocol.file.allow=always", *arguments, cwd=path
        )
        assert result.returncode == 0, (arguments, result.stderr)
        return result.stdout


@contextmanager
def _writable_seed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selected: tuple[RunConcurrencyPolicy, Path, Path],
    *,
    expected_tasks: tuple[str, ...] | None = None,
    transfer: _TransferredBranch | None = None,
    notes_to_publish: tuple[str, str] | None = None,
    withheld_context: str | None = None,
) -> Generator[_WritableSeed, None, None]:
    policy, program, runtime_root = selected
    cli = Path(sys.executable).with_name("oh-goon")
    assert cli.is_file()
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(tmp_path / "league.yml"))
    monkeypatch.setenv("HASHMARKS_NO_UPDATE_CHECK", "1")

    incoming_seed = _ORIGINAL_WRITABLE_SOURCE if transfer is None else _ACCEPTED_WRITABLE_SEED
    accepted_seed = _ACCEPTED_WRITABLE_SEED if transfer is None else _FIRST_WRITABLE_EDIT
    following_edits = (
        (
            (_ACCEPTED_WRITABLE_SEED, _FIRST_WRITABLE_EDIT),
            (_FIRST_WRITABLE_EDIT, _SECOND_WRITABLE_EDIT),
        )
        if transfer is None
        else ((_FIRST_WRITABLE_EDIT, _SECOND_WRITABLE_EDIT),)
    )
    edits = {
        "a": ((incoming_seed, accepted_seed),),
        "b": ((incoming_seed, b"VALUE = 43\n"),),
        "third": following_edits,
    }
    calls = {label: 0 for label in edits}
    notes = dict(zip(("a", "b"), notes_to_publish, strict=True)) if notes_to_publish else {}

    def invoke(name: str, arguments: dict[str, Any], call_id: str) -> tuple[dict[str, Any], str]:
        return {
            "role": "assistant",
            "tool_calls": [
                {
                    "index": 0,
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ],
        }, "tool_calls"

    def model_turn(label: str, body: dict[str, Any]) -> tuple[dict[str, Any], str]:
        tools_returned = [item["content"] for item in body["messages"] if item["role"] == "tool"]
        if withheld_context is not None:
            assert withheld_context not in json.dumps(body)
        if label in notes and not tools_returned:
            assert "agent_notes_publish" in {item["function"]["name"] for item in body["tools"]}
            return invoke(
                "agent_notes_publish",
                {"request_id": f"handoff-{label}", "kind": "observation", "body": notes[label]},
                f"publish_{label}",
            )
        if len(tools_returned) > int(label in notes):
            assert f"edited-{label}-{calls[label]}" in json.dumps(tools_returned)
            return {"role": "assistant", "content": "The candidate edit is ready."}, "stop"
        if label in notes:
            assert f"handoff-{label}" in json.dumps(tools_returned)

        if label == "third" and expected_tasks is not None:
            assert expected_tasks[calls[label]] in json.dumps(body["messages"])
        incoming, outgoing = edits[label][calls[label]]
        calls[label] += 1
        tool = next(
            item["function"]
            for item in body["tools"]
            if item["function"]["name"] in {"bash", "shell", "shell_command", "exec_command"}
        )
        properties = tool["parameters"]["properties"]
        command = shlex.join(
            (
                "/usr/bin/python3",
                "-c",
                "from pathlib import Path\n"
                "target = Path('code.py')\n"
                f"assert target.read_bytes() == {incoming!r}\n"
                f"target.write_bytes({outgoing!r})\n"
                f"print('edited-{label}-{calls[label]}')\n",
            )
        )
        arguments = {"command" if "command" in properties else "cmd": command}
        if "description" in properties:
            arguments["description"] = "Edit the exact incoming candidate"
        return invoke(tool["name"], arguments, f"edit_{label}_{calls[label]}")

    with native_two_run_provider(
        tmp_path,
        policy,
        program=program,
        public_runtime=runtime_root,
        same_repository=True,
        model_turn=model_turn,
    ) as provider:
        repository = provider.repositories["a"]
        native = _WritableSeed.native
        bare = tmp_path / "branch-owner.git"
        checkout = tmp_path / "receiving"
        ref = "refs/heads/qualification/writable"
        if transfer is None:
            native(
                tmp_path, "clone", "--bare", "--local", "--no-hardlinks", str(repository), str(bare)
            )
            native(bare, "update-ref", ref, native(repository, "rev-parse", "HEAD"))
        else:
            repository = tmp_path / "imported-source"
            native(tmp_path, "init", "--bare", "--quiet", str(bare))
            native(bare, "bundle", "verify", str(transfer.bundle))
            native(
                bare,
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                str(transfer.bundle),
                f"{transfer.ref}:{ref}",
            )
            assert native(bare, "show-ref") == f"{transfer.commit} {ref}"
            native(bare, "symbolic-ref", "HEAD", ref)
            native(tmp_path, "clone", "--local", "--no-hardlinks", str(bare), str(repository))
            assert native(repository, "rev-parse", "HEAD^{tree}") == transfer.tree
        original = native(repository, "rev-parse", "HEAD")
        if transfer is not None:
            assert original == transfer.commit
        original_refs = native(repository, "show-ref", "--head")
        native(tmp_path, "clone", "--local", "--no-hardlinks", str(bare), str(checkout))
        native(checkout, "switch", "--detach", original)
        for path in (repository, checkout):
            code, stdout, stderr = _operator(cli, path, "league", "add-folder", str(path))
            assert code == 0, (stdout, stderr)

        catalog = tmp_path / "provider.json"
        _write_catalog(catalog, provider)
        verifier_bytes = (
            "from pathlib import Path\n"
            "import sys\n"
            "assert Path(sys.argv[1], 'code.py').read_bytes().splitlines()[0] == b'VALUE = 42'\n"
        ).encode()
        executable = Path("/usr/bin/python3").resolve(strict=True)
        work = ParallelAgentWorkRequest(
            work_id="native-writable-seed" if transfer is None else "native-imported-seed",
            workspace=repository,
            expected_base_revision=original,
            task="Produce the fixture value accepted by the independent verifier.",
            briefs=("Use the accepted value", "Use a competing value"),
            native_profiles=(
                provider.selected["a"].profile_name,
                provider.selected["b"].profile_name,
            ),
            provider_catalog=catalog,
            capacity=policy,
            notes_enabled=bool(notes),
            verifier=ParallelCandidateVerifier(
                acceptance="The first line of the fixture must assign VALUE = 42.",
                executable=str(executable),
                executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
                arguments=("-I", "{input:verify.py}", "{candidate}"),
                inputs=(
                    FrozenVerifierInput(
                        name="verify.py",
                        content=verifier_bytes.decode(),
                        sha256=hashlib.sha256(verifier_bytes).hexdigest(),
                    ),
                ),
            ),
        )
        request_path = tmp_path / "round-seed.json"
        request_path.write_text(work.model_dump_json(), encoding="utf-8")
        seeded = _cli_json(
            cli, repository, "runs", "start-parallel", str(request_path), expected_status=1
        )
        winner, loser = seeded["children"]
        assert [winner["outcome"], loser["outcome"]] == ["SUCCESS", "ATTENTION_REQUIRED"]
        accepted = _cli_json(cli, repository, "runs", "verify-candidate", winner["fleet_run_id"])
        failed = _cli_json(
            cli, repository, "runs", "verify-candidate", loser["fleet_run_id"], expected_status=1
        )
        assert accepted["outcome"] == "PASS" and failed["outcome"] == "FAIL"

        yield _WritableSeed(
            cli,
            repository,
            original,
            original_refs,
            bare,
            checkout,
            ref,
            catalog,
            provider,
            accepted,
            failed,
            calls,
        )


def _advance_seed(seed: _WritableSeed, advance_id: str) -> BranchAdvanceReceipt:
    runtime = ApplicationRuntime.build(standard_local=True, startup_purpose="workflow")
    try:
        return advance_verified_candidate(
            BranchAdvanceRequest(
                advance_id=advance_id,
                fleet_run_id=seed.accepted["fleet_run_id"],
                checkpoint_id=seed.accepted["checkpoint_id"],
                branch_owner=seed.bare,
                workspace=seed.checkout,
                branch_ref=seed.ref,
                expected_tip=seed.original,
            ),
            database=LocalDatabase(seed.provider.database_path),
            repositories=runtime.repositories,
        )
    finally:
        runtime.shutdown()


def _writable_runtime(seed: _WritableSeed) -> ApplicationRuntime:
    _, isolation, git = resolve_native_cli_selection(
        agent="pi",
        profile=seed.provider.selected["third"].profile_name,
        workspace=seed.checkout,
        config_file=seed.catalog,
        session=PiSessionIntent(mode="ephemeral"),
    )
    assert isolation is not None
    return ApplicationRuntime.build(
        standard_local=True, standard_process_isolation=isolation, native_git_service=git
    )


_RECOVER_RECIPIENT_SCHEDULE = """
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_ai import resolve_native_cli_selection
from oh_goon.core.models.agent_providers import PiSessionIntent
from oh_goon.core.vocabulary import FleetRunSource, ScheduleExecutionStatus
from oh_goon.services import schedule_timing

_, isolation, git = resolve_native_cli_selection(
    agent="pi", profile=sys.argv[2], workspace=Path(sys.argv[1]),
    config_file=Path(sys.argv[3]), session=PiSessionIntent(mode="ephemeral"),
)
assert isolation is not None
now = datetime.fromisoformat(sys.argv[4])
schedule_timing.utc_now = lambda: now
runtime = ApplicationRuntime.build(
    standard_local=True, standard_process_isolation=isolation, native_git_service=git,
)
try:
    runtime.schedules.tick()
    (status,) = runtime.schedules.catalog().schedules
    latest = status.latest_occurrence
    assert latest is not None and latest.status is ScheduleExecutionStatus.COMPLETED
    assert latest.finished_at is not None
    now = latest.finished_at + timedelta(minutes=2)
    runtime.schedules.tick()
    (final,) = runtime.schedules.catalog().schedules
    assert final.latest_occurrence == latest and final.next_run_at is None
    history = runtime.runs.history(source=FleetRunSource.SCHEDULED).items
    print(json.dumps({
        "occurrence": latest.schedule_run_id,
        "run_ids": [item.run.fleet_run_id for item in history],
    }))
finally:
    runtime.shutdown()
"""


@pytest.mark.certification
@pytest.mark.no_resume
def test_installed_writable_rounds_publish_before_original_successor_admission(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected_pi_environment(request, timeout=360)
    if selected is None:
        return
    with _writable_seed(tmp_path, monkeypatch, selected) as seed:
        cli, repository = seed.cli, seed.repository
        original, original_refs = seed.original, seed.original_refs
        native, bare, checkout, ref = seed.native, seed.bare, seed.checkout, seed.ref
        catalog, provider = seed.catalog, seed.provider
        accepted, failed = seed.accepted, seed.failed

        def advance_args(advance_id: str, result: dict[str, Any], tip: str) -> tuple[str, ...]:
            return (
                "runs",
                "advance-candidate",
                result["fleet_run_id"],
                "--advance-id",
                advance_id,
                "--checkpoint-id",
                result["checkpoint_id"],
                "--branch-owner",
                str(bare),
                "--workspace",
                str(checkout),
                "--branch-ref",
                ref,
                "--expected-tip",
                tip,
                "--authorize",
            )

        refused = _cli_json(
            cli, checkout, *advance_args("failed-seed", failed, original), expected_status=2
        )
        assert refused["status"] == "rejected"
        assert native(bare, "rev-parse", ref) == original

        # Simulate loss of the database acknowledgement only after native Git publishes.
        def lose_publication_ack(
            _store: BranchAdvanceStore, preparation: PreparedBranchAdvance, *, observed_tip: str
        ) -> None:
            assert observed_tip == preparation.commit
            raise OSError("simulated lost publication acknowledgement")

        first_request = BranchAdvanceRequest(
            advance_id="native-round-1",
            fleet_run_id=accepted["fleet_run_id"],
            checkpoint_id=accepted["checkpoint_id"],
            branch_owner=bare,
            workspace=checkout,
            branch_ref=ref,
            expected_tip=original,
        )
        runtime = ApplicationRuntime.build(standard_local=True, startup_purpose="workflow")
        try:
            with monkeypatch.context() as patch:
                patch.setattr(BranchAdvanceStore, "confirm", lose_publication_ack)
                with pytest.raises(OSError, match="simulated lost publication acknowledgement"):
                    advance_verified_candidate(
                        first_request,
                        database=LocalDatabase(provider.database_path),
                        repositories=runtime.repositories,
                    )
        finally:
            runtime.shutdown()
        prepared_before_replay = _cli_json(
            cli, checkout, "runs", "branch-advance", first_request.advance_id
        )
        assert prepared_before_replay["published_at"] is None
        first_commit = prepared_before_replay["preparation"]["commit"]
        assert native(bare, "rev-parse", ref) == first_commit
        assert (
            native(bare, "rev-parse", f"refs/oh-goon/branch-advances/{first_commit}")
            == first_commit
        )
        first_publication_refs = native(bare, "show-ref")

        tip = original
        seen_processes: set[str] = set()
        for index, (incoming, outgoing) in enumerate(
            zip(
                (_ACCEPTED_WRITABLE_SEED, _FIRST_WRITABLE_EDIT),
                (_FIRST_WRITABLE_EDIT, _SECOND_WRITABLE_EDIT),
                strict=True,
            ),
            start=1,
        ):
            advance_id = f"native-round-{index}"
            advance = advance_args(advance_id, accepted, tip)
            receipt = _cli_json(cli, checkout, *advance)
            assert receipt["published_at"] and receipt["next_run"] is None
            prepared = receipt["preparation"]
            commit = prepared["commit"]
            if index == 1:
                assert prepared == prepared_before_replay["preparation"]
                assert native(bare, "show-ref") == first_publication_refs
            assert prepared["verification"] == accepted
            assert prepared["expected_tip"] == tip
            assert native(bare, "rev-parse", ref) == commit
            assert native(bare, "rev-parse", f"{commit}^") == tip
            assert native(bare, "rev-parse", f"{commit}^{{tree}}") == prepared["tree"]
            assert native(checkout, "rev-parse", "HEAD") == tip
            assert _cli_json(cli, checkout, "runs", "branch-advance", advance_id) == receipt

            start = (
                "runs",
                "start-round",
                advance_id,
                "--profile",
                provider.selected["third"].profile_name,
                "--config",
                str(catalog),
                "--authorize",
                "--prompt",
                f"Apply the fixture's verified round {index}.",
            )
            run = _cli_json(cli, checkout, *start)
            run_id = run["fleet_run_id"]
            assert run["outcome"] == "SUCCESS"
            readback = _cli_json(cli, checkout, "runs", "branch-advance", advance_id)
            assert readback["next_run"]["fleet_run_id"] == run_id
            assert readback["published_at"] == receipt["published_at"]
            events = _run_events(cli, checkout, run_id)
            (admission,) = [item for item in events if item["event_type"] == "RUN_ADMITTED"]
            assert datetime.fromisoformat(receipt["published_at"]) <= datetime.fromisoformat(
                admission["generated_at"]
            )
            (admitted_repository,) = admission["repository_admissions"]
            assert (
                readback["next_run"]["repository_subject_id"]
                == (admitted_repository["source_authority"]["subject_instance_id"])
            )
            (run_steps,) = _cli_json(cli, checkout, "runs", "steps", run_id)["repositories"]
            assert [
                (step["step_id"].rsplit(".", 1)[-1], step["status"]) for step in run_steps["steps"]
            ] == [
                ("verify-base", "PASSED"),
                ("native-agent", "PASSED"),
                ("verify-1", "PASSED"),
            ]
            started = {
                item["physical_execution_id"]
                for item in events
                if item["event_type"] == "PROCESS_STARTED"
            }
            assert started and not seen_processes.intersection(started)
            seen_processes.update(started)
            assert native(checkout, "rev-parse", "HEAD") == commit
            assert (checkout / "code.py").read_bytes() == incoming
            candidate = _cli_json(cli, checkout, "runs", "candidate", run_id)
            assert (Path(candidate["path"]) / "code.py").read_bytes() == outgoing
            accepted = _cli_json(cli, checkout, "runs", "verify-candidate", run_id)
            assert accepted["outcome"] == "PASS" and accepted["base_revision"] == commit
            assert accepted["verifier_id"] == prepared["verification"]["verifier_id"]

            before_replay = native(bare, "show-ref")
            requests = {label: len(items) for label, items in provider.requests.items()}
            assert _cli_json(cli, checkout, *advance) == readback
            assert _cli_json(cli, checkout, *start) == run
            assert _run_events(cli, checkout, run_id) == events
            changed = _cli_json(
                cli, checkout, *start[:-1], "Use a different task", expected_status=2
            )
            assert changed["status"] == "rejected"
            assert native(bare, "show-ref") == before_replay
            assert {label: len(items) for label, items in provider.requests.items()} == requests
            tip = commit

        assert accepted["base_revision"] == tip
        (Path(candidate["path"]) / "code.py").write_bytes(
            _SECOND_WRITABLE_EDIT + b"# Unverified change\n"
        )
        final_refs = native(bare, "show-ref")
        stale = _cli_json(
            cli, checkout, *advance_args("changed-candidate", accepted, tip), expected_status=2
        )
        assert stale["status"] == "rejected"
        assert native(bare, "show-ref") == final_refs
        assert native(repository, "show-ref", "--head") == original_refs
        assert (repository / "code.py").read_bytes() == _ORIGINAL_WRITABLE_SOURCE
        assert seed.calls == {"a": 1, "b": 1, "third": 2}
        assert not provider.errors, provider.errors


class _InterruptedController(BaseException):
    """Exit at a durable native effect boundary before caller acknowledgement."""


@pytest.mark.certification
@pytest.mark.no_resume
def test_installed_writable_schedule_restarts_without_repeating_admitted_round(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.core.models.writable_schedule import WritableScheduleTask

    selected = _selected_pi_environment(request, timeout=360)
    if selected is None:
        return
    first_task = "Add the first scheduled round annotation in code.py."
    second_task = "Add the second scheduled round annotation in code.py."
    with _writable_seed(
        tmp_path, monkeypatch, selected, expected_tasks=(first_task, second_task)
    ) as seed:
        database = LocalDatabase(seed.provider.database_path)
        advances = BranchAdvanceStore(database)
        runtime = _writable_runtime(seed)

        def current_occurrence() -> tuple[ScheduleOccurrence, ScheduleOccurrenceRepositoryRun]:
            (status,) = runtime.schedules.catalog().schedules
            occurrence = status.latest_occurrence
            assert occurrence is not None
            repositories = runtime.schedules.occurrence_repositories(
                occurrence.schedule_id, occurrence.schedule_run_id
            )
            (child,) = repositories.items
            return occurrence, child

        def assert_successful_run(run_id: str) -> None:
            detail = runtime.runs.wait_for_terminal(run_id, timeout=60)
            assert detail.outcome is FleetRunOutcome.SUCCESS, detail.model_dump_json()[-12000:]
            assert detail.run.source is FleetRunSource.SCHEDULED
            (tape,) = detail.game_tape.repositories
            assert [
                (step.step_id.rsplit(".", 1)[-1], step.status.value) for step in tape.steps
            ] == [
                ("verify-base", "PASSED"),
                ("native-agent", "PASSED"),
                ("verify-1", "PASSED"),
            ]
            events = _run_events(seed.cli, seed.checkout, run_id)
            (admission,) = [item for item in events if item["event_type"] == "RUN_ADMITTED"]
            receipt = advances.find_by_next_run(run_id)
            assert receipt is not None and receipt.published_at is not None
            assert receipt.next_run is not None
            (repository,) = admission["repository_admissions"]
            assert repository["source_authority"]["subject_instance_id"] == (
                receipt.next_run.repository_subject_id
            )
            assert receipt.published_at <= datetime.fromisoformat(admission["generated_at"])

        try:
            initial = advance_verified_candidate(
                BranchAdvanceRequest(
                    advance_id="scheduled-seed",
                    fleet_run_id=seed.accepted["fleet_run_id"],
                    checkpoint_id=seed.accepted["checkpoint_id"],
                    branch_owner=seed.bare,
                    workspace=seed.checkout,
                    branch_ref=seed.ref,
                    expected_tip=seed.original,
                ),
                database=database,
                repositories=runtime.repositories,
            )
            assert initial.published_at is not None and initial.next_run is None
            definition = runtime.schedules.create_writable_rounds(
                "writable-timer",
                seed_advance_id=initial.preparation.advance_id,
                tasks=(
                    WritableScheduleTask(task_id="first-edit", prompt=first_task),
                    WritableScheduleTask(task_id="second-edit", prompt=second_task),
                ),
                interval_minutes=1,
            )
            first_due = definition.created_at + timedelta(minutes=1)
            now = first_due - timedelta(microseconds=1)
            monkeypatch.setattr(schedule_timing, "utc_now", lambda: now)
            runtime.schedules.tick()
            (initial_status,) = runtime.schedules.catalog().schedules
            assert initial_status.next_run_at == first_due
            assert initial_status.latest_occurrence is None and seed.calls["third"] == 0

            original_start = FleetRunControlPlane._start_intent

            def interrupt_after_admission(
                control: FleetRunControlPlane, *args: Any, **kwargs: Any
            ) -> None:
                admitted = original_start(control, *args, **kwargs)
                assert admitted.source is FleetRunSource.SCHEDULED
                raise _InterruptedController("lost Fleet admission acknowledgement")

            now = first_due
            with monkeypatch.context() as patch:
                patch.setattr(FleetRunControlPlane, "_start_intent", interrupt_after_admission)
                with pytest.raises(_InterruptedController, match="lost Fleet admission"):
                    runtime.schedules.tick()
            first, first_child = current_occurrence()
            assert first.status is ScheduleExecutionStatus.RUNNING
            assert first_child.status is ScheduleRepositoryRunStatus.RUNNING
            first_id = first_child.fleet_run_id
            assert first_id is not None
            assert_successful_run(first_id)
            assert current_occurrence() == (first, first_child)
            first_events = _run_events(seed.cli, seed.checkout, first_id)
            runtime.shutdown()

            runtime = _writable_runtime(seed)
            runtime.schedules.tick()
            settled, settled_child = current_occurrence()
            assert settled.schedule_run_id == first.schedule_run_id
            assert settled.status is ScheduleExecutionStatus.COMPLETED
            assert settled_child.fleet_run_id == first_id
            assert settled.finished_at is not None
            second_due = settled.finished_at + timedelta(minutes=1)
            now = second_due - timedelta(microseconds=1)
            runtime.schedules.tick()
            assert current_occurrence() == (settled, settled_child)
            assert _run_events(seed.cli, seed.checkout, first_id) == first_events
            assert seed.calls["third"] == 1

            interrupted_advances: list[PreparedBranchAdvance] = []

            def interrupt_after_publish(
                _store: BranchAdvanceStore, preparation: PreparedBranchAdvance, *, observed_tip: str
            ) -> None:
                assert preparation.verification.fleet_run_id == first_id
                assert preparation.expected_tip == initial.preparation.commit
                assert observed_tip == seed.native(seed.bare, "rev-parse", seed.ref)
                interrupted_advances.append(preparation)
                raise _InterruptedController("lost native branch acknowledgement")

            now = second_due
            with monkeypatch.context() as patch:
                patch.setattr(BranchAdvanceStore, "confirm", interrupt_after_publish)
                with pytest.raises(_InterruptedController, match="lost native branch"):
                    runtime.schedules.tick()
            (prepared,) = interrupted_advances
            interrupted, pending = current_occurrence()
            assert interrupted.status is ScheduleExecutionStatus.RUNNING
            assert pending.status is ScheduleRepositoryRunStatus.PENDING
            assert pending.fleet_run_id is None
            original_receipt = advances.find(prepared.advance_id)
            assert original_receipt is not None and original_receipt.published_at is None
            assert seed.calls["third"] == 1
            refs = seed.native(seed.bare, "show-ref")
            runtime.shutdown()

            runtime = _writable_runtime(seed)
            runtime.schedules.tick()
            resumed, second_child = current_occurrence()
            assert resumed.schedule_run_id == interrupted.schedule_run_id
            second_id = second_child.fleet_run_id
            assert second_id is not None and second_id != first_id
            assert_successful_run(second_id)
            receipt = advances.find_by_next_run(second_id)
            assert receipt is not None and receipt.preparation == prepared
            assert seed.native(seed.bare, "show-ref") == refs
            assert seed.native(seed.checkout, "rev-parse", "HEAD") == prepared.commit
            assert (seed.checkout / "code.py").read_bytes() == _FIRST_WRITABLE_EDIT
            runtime.schedules.tick()
            resumed, second_child = current_occurrence()
            assert resumed.status is ScheduleExecutionStatus.COMPLETED
            assert second_child.fleet_run_id == second_id
            assert resumed.finished_at is not None
            now = resumed.finished_at + timedelta(minutes=2)
            runtime.schedules.tick()
            assert current_occurrence() == (resumed, second_child)
            history = runtime.runs.history(source=FleetRunSource.SCHEDULED)
            assert tuple(item.run.fleet_run_id for item in history.items) == (second_id, first_id)
            assert seed.calls == {"a": 1, "b": 1, "third": 2}
            assert not seed.provider.errors, seed.provider.errors
            assert _run_events(seed.cli, seed.checkout, first_id) == first_events
            assert seed.native(seed.bare, "show-ref") == refs
            assert seed.native(seed.repository, "show-ref", "--head") == seed.original_refs
        finally:
            runtime.shutdown()


@pytest.mark.certification
@pytest.mark.no_resume
def test_installed_offline_schedule_handoff_uses_recipient_verification_and_recovers_once(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rehearse two independent state roots and two recovery cuts on one Linux host."""
    from oh_goon.core.models.writable_schedule import WritableScheduleTask

    selected = _selected_pi_environment(request, timeout=420)
    if selected is None:
        return
    selected_context = "Keep VALUE equal to 42 and record the receiving handoff."
    withheld_context = "withheld-donor-context-sentinel"
    handoff = tmp_path / "handoff"
    handoff.mkdir(mode=0o700)
    with _writable_seed(
        tmp_path / "donor",
        monkeypatch,
        selected,
        notes_to_publish=(selected_context, withheld_context),
    ) as donor:
        donor_paths = _environment(_STATE_ENV)
        donor_database = LocalDatabase(donor.provider.database_path, read_only=True)
        donor_store = BranchAdvanceStore(donor_database)
        advance_id = "donor-handoff"

        def interrupt_after_donor_publication(
            _store: BranchAdvanceStore, preparation: PreparedBranchAdvance, *, observed_tip: str
        ) -> None:
            assert (
                observed_tip
                == preparation.commit
                == donor.native(donor.bare, "rev-parse", donor.ref)
            )
            raise _InterruptedController("lost donor publication acknowledgement")

        with monkeypatch.context() as patch:
            patch.setattr(BranchAdvanceStore, "confirm", interrupt_after_donor_publication)
            with pytest.raises(_InterruptedController, match="lost donor publication"):
                _advance_seed(donor, advance_id)
        pending = donor_store.find(advance_id)
        assert pending is not None and pending.published_at is None
        donor_refs = donor.native(donor.bare, "show-ref")
        published = _advance_seed(donor, advance_id)
        assert published.preparation == pending.preparation and published.published_at is not None
        assert published.next_run is None
        assert donor.native(donor.bare, "show-ref") == donor_refs

        transfer = _TransferredBranch(
            handoff / "approved.bundle",
            donor.ref,
            published.preparation.commit,
            published.preparation.tree,
        )
        donor.native(donor.bare, "bundle", "create", str(transfer.bundle), transfer.ref)
        assert donor.native(donor.bare, "bundle", "list-heads", str(transfer.bundle)) == (
            f"{transfer.commit} {transfer.ref}"
        )
        work_id = donor.accepted["parallel_work_id"]
        notes = AgentNotesStore(donor_database, Journal(donor_database)).read_page(work_id).notes
        assert {note.body for note in notes} == {selected_context, withheld_context}
        (approved,) = [note for note in notes if note.body == selected_context]
        document = handoff / "approved.md"
        export = _cli_json(
            donor.cli,
            donor.repository,
            "runs",
            "parallel-notes",
            work_id,
            "--export-note",
            str(approved.receipt.note_id),
            "--output",
            str(document),
        )
        markdown = document.read_bytes()
        assert export["note_ids"] == [approved.receipt.note_id]
        assert export["sha256"] == hashlib.sha256(markdown).hexdigest()
        assert b"UNTRUSTED_AGENT_CONTEXT" in markdown and selected_context.encode() in markdown
        assert withheld_context.encode() not in markdown
        assert not donor.provider.errors, donor.provider.errors
    # Only the selected files cross this point. Donor's controller and provider are stopped.
    donor_view = Journal(donor_database).read_view()

    with _writable_seed(
        tmp_path / "recipient",
        monkeypatch,
        selected,
        expected_tasks=(selected_context,),
        transfer=transfer,
        withheld_context=withheld_context,
    ) as recipient:
        recipient_paths = _environment(_STATE_ENV)
        for name in (
            "OH_GOON_DB",
            "OH_GOON_LEAGUE_FILE",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_STATE_HOME",
            "XDG_CACHE_HOME",
        ):
            assert recipient_paths[name] != donor_paths[name]
        assert recipient.original == transfer.commit
        assert (recipient.repository / "code.py").read_bytes() == _ACCEPTED_WRITABLE_SEED
        assert recipient.accepted["base_revision"] == transfer.commit
        assert donor_view.run_detail(recipient.accepted["fleet_run_id"]) is None

        # The current entrypoint requires an original local candidate. Bootstrap from the
        # imported commit with a real recipient worker and native verifier before publication.
        bootstrap = _advance_seed(recipient, "recipient-handoff")
        assert bootstrap.published_at is not None and bootstrap.next_run is None
        assert bootstrap.preparation.verification.fleet_run_id == recipient.accepted["fleet_run_id"]
        assert recipient.native(
            recipient.bare, "rev-parse", f"{bootstrap.preparation.commit}^"
        ) == (transfer.commit)
        recipient_database = LocalDatabase(recipient.provider.database_path, read_only=True)
        assert BranchAdvanceStore(recipient_database).find(advance_id) is None
        assert (
            Journal(recipient_database).read_view().run_detail(donor.accepted["fleet_run_id"])
            is None
        )
        imported_markdown = tmp_path / "recipient" / "approved.md"
        imported_markdown.write_bytes(markdown)
        prompt = (
            "Apply the approved receiving change. Use this selected Markdown as attributed context.\n\n"
            + imported_markdown.read_text(encoding="utf-8")
        )
        tasks = (WritableScheduleTask(task_id="receiving-edit", prompt=prompt),)
        runtime = _writable_runtime(recipient)
        try:
            with pytest.raises(StateConflict, match="original published branch advance"):
                runtime.schedules.create_writable_rounds(
                    "foreign-receipt", seed_advance_id=advance_id, tasks=tasks, interval_minutes=1
                )
            assert runtime.schedules.catalog().schedules == ()
            definition = runtime.schedules.create_writable_rounds(
                "recipient-writable",
                seed_advance_id=bootstrap.preparation.advance_id,
                tasks=tasks,
                interval_minutes=1,
            )
            now = definition.created_at + timedelta(minutes=1)
            monkeypatch.setattr(schedule_timing, "utc_now", lambda: now)
            start = FleetRunControlPlane._start_intent

            def interrupt_after_recipient_admission(
                control: FleetRunControlPlane, *args: Any, **kwargs: Any
            ) -> None:
                assert start(control, *args, **kwargs).source is FleetRunSource.SCHEDULED
                raise _InterruptedController("lost recipient admission acknowledgement")

            with monkeypatch.context() as patch:
                patch.setattr(
                    FleetRunControlPlane, "_start_intent", interrupt_after_recipient_admission
                )
                with pytest.raises(_InterruptedController, match="lost recipient admission"):
                    runtime.schedules.tick()
            (status,) = runtime.schedules.catalog().schedules
            pending_occurrence = status.latest_occurrence
            assert pending_occurrence is not None
            (pending_child,) = runtime.schedules.occurrence_repositories(
                definition.schedule_id, pending_occurrence.schedule_run_id
            ).items
            run_id = pending_child.fleet_run_id
            assert (
                pending_child.status is ScheduleRepositoryRunStatus.RUNNING and run_id is not None
            )
            detail = runtime.runs.wait_for_terminal(run_id, timeout=60)
            assert detail.outcome is FleetRunOutcome.SUCCESS, detail.model_dump_json()[-12000:]
            target = detail.run.target
            assert isinstance(target, FleetPlanTarget) and target.candidate_workspace is not None
            assert (
                target.candidate_workspace.workspace.base_revision == bootstrap.preparation.commit
            )
            (agent,) = [step for step in target.plan.steps if isinstance(step, ResolvedAgentStep)]
            assert agent.task_sha256 == tasks[0].prompt_sha256
            (tape,) = detail.game_tape.repositories
            assert [
                (step.step_id.rsplit(".", 1)[-1], step.status.value) for step in tape.steps
            ] == [
                ("verify-base", "PASSED"),
                ("native-agent", "PASSED"),
                ("verify-1", "PASSED"),
            ]
            events = _run_events(recipient.cli, recipient.checkout, run_id)
            assert sum(event["event_type"] == "RUN_ADMITTED" for event in events) == 1
            runtime.shutdown()

            code, output, errors = _operator(
                Path(sys.executable),
                recipient.checkout,
                "-I",
                "-c",
                _RECOVER_RECIPIENT_SCHEDULE,
                str(recipient.checkout),
                recipient.provider.selected["third"].profile_name,
                str(recipient.catalog),
                now.isoformat(),
                environment={**_environment(_HOST_ENV), **recipient_paths},
                timeout=60,
            )
            assert code == 0, (output[-4096:], errors)
            assert json.loads(output) == {
                "occurrence": pending_occurrence.schedule_run_id,
                "run_ids": [run_id],
            }
            assert _run_events(recipient.cli, recipient.checkout, run_id) == events
            assert recipient.calls["third"] == 1 and not recipient.provider.errors
            verified = _cli_json(
                recipient.cli, recipient.checkout, "runs", "verify-candidate", run_id
            )
            assert verified["outcome"] == "PASS"
            assert donor_view.run_detail(run_id) is None
        finally:
            runtime.shutdown()

    after = Journal(donor_database).read_view()
    assert after.session_id == donor_view.session_id and after.revision == donor_view.revision
    assert BranchAdvanceStore(donor_database).find(advance_id) == published
    assert donor.native(donor.bare, "show-ref") == donor_refs
    assert donor.native(donor.repository, "show-ref", "--head") == donor.original_refs


@pytest.mark.certification
@pytest.mark.no_resume
def test_installed_writable_schedule_refills_only_from_later_approved_tasks(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.core.models.writable_schedule import WritableScheduleTask

    selected = _selected_pi_environment(request, timeout=480)
    if selected is None:
        return
    first_prompt = "Record the first approved change to code.py."
    second_instruction = "Record the second approved change to code.py."
    note_body = "Keep VALUE equal to 42 and preserve the verified predecessor."
    withheld = "withheld-refill-context-sentinel"

    with _writable_seed(
        tmp_path / "receiver",
        monkeypatch,
        selected,
        expected_tasks=(first_prompt, note_body),
        notes_to_publish=(note_body, withheld),
    ) as seed:
        database = LocalDatabase(seed.provider.database_path, read_only=True)
        branch_advances = BranchAdvanceStore(database)
        seed_receipt = _advance_seed(seed, "approved-refill-seed")
        work_id = seed.accepted["parallel_work_id"]
        notes = AgentNotesStore(database, Journal(database)).read_page(work_id).notes
        (approved,) = [note for note in notes if note.body == note_body]
        staged_note = tmp_path / "approved.md"
        exported = _cli_json(
            seed.cli,
            seed.repository,
            "runs",
            "parallel-notes",
            work_id,
            "--export-note",
            str(approved.receipt.note_id),
            "--output",
            str(staged_note),
        )
        note_bytes = staged_note.read_bytes()
        assert exported["note_ids"] == [approved.receipt.note_id]
        assert exported["sha256"] == hashlib.sha256(note_bytes).hexdigest()
        assert b"UNTRUSTED_AGENT_CONTEXT" in note_bytes and note_body.encode() in note_bytes
        assert withheld.encode() not in note_bytes

        first_task = WritableScheduleTask(task_id="first-change", prompt=first_prompt)
        second_task = WritableScheduleTask(
            task_id="second-change",
            prompt=f"{second_instruction}\n\n{note_bytes.decode('utf-8')}",
        )
        approval_path = tmp_path / "approved-tasks.json"
        runtime = _writable_runtime(seed)

        def current_occurrence() -> tuple[ScheduleOccurrence, ScheduleOccurrenceRepositoryRun]:
            (status,) = runtime.schedules.catalog().schedules
            occurrence = status.latest_occurrence
            assert occurrence is not None
            page = runtime.schedules.occurrence_repositories(
                occurrence.schedule_id, occurrence.schedule_run_id
            )
            (child,) = page.items
            return occurrence, child

        def assert_fresh_run(run_id: str, task: WritableScheduleTask, base: str) -> None:
            detail = runtime.runs.wait_for_terminal(run_id, timeout=60)
            assert (
                detail.outcome is FleetRunOutcome.SUCCESS
                and detail.run.source is FleetRunSource.SCHEDULED
            )
            target = detail.run.target
            assert isinstance(target, FleetPlanTarget) and target.candidate_workspace is not None
            assert target.candidate_workspace.workspace.base_revision == base
            (agent,) = [step for step in target.plan.steps if isinstance(step, ResolvedAgentStep)]
            assert agent.task_sha256 == task.prompt_sha256
            assert isinstance(agent.workload, ResolvedNativeAgentWorkload)
            assert agent.workload.native_agent.session == PiSessionIntent(mode="ephemeral")
            operation = detail.operations[0].evidence
            assert isinstance(operation, FleetPlanOperationEvidence)
            (native_step,) = [
                step for step in operation.plan.steps if step.step_id == agent.step_id
            ]
            assert "--no-session" in native_step.argv
            assert not {"--session", "--session-dir", "--resume", "--continue"}.intersection(
                native_step.argv
            )

        try:
            definition = runtime.schedules.create_writable_rounds(
                "approved-task-refill",
                seed_advance_id=seed_receipt.preparation.advance_id,
                tasks=(first_task,),
                interval_minutes=1,
            )
            now = definition.created_at + timedelta(minutes=1)
            monkeypatch.setattr(schedule_timing, "utc_now", lambda: now)
            runtime.schedules.tick()
            _, first_child = current_occurrence()
            first_id = first_child.fleet_run_id
            assert first_id is not None
            assert_fresh_run(first_id, first_task, seed_receipt.preparation.commit)
            runtime.schedules.tick()
            first, first_child = current_occurrence()
            assert (
                first.status is ScheduleExecutionStatus.COMPLETED and first.finished_at is not None
            )
            first_verified = _cli_json(
                seed.cli, seed.checkout, "runs", "verify-candidate", first_id
            )
            assert first_verified["outcome"] == "PASS"
            assert first_verified["base_revision"] == seed_receipt.preparation.commit
            first_events = _run_events(seed.cli, seed.checkout, first_id)
            assert sum(event["event_type"] == "RUN_ADMITTED" for event in first_events) == 1
            original_refs = seed.native(seed.bare, "show-ref")

            runtime.schedules.bind_approved_task_document(
                definition.schedule_id,
                expected_generation_id=definition.generation_id,
                path=approval_path,
            )
            now = first.finished_at + timedelta(minutes=1)
            runtime.schedules.tick()
            assert current_occurrence() == (first, first_child)
            approval_path.write_text('{"tasks": []}', encoding="utf-8")
            runtime.schedules.tick()
            assert current_occurrence() == (first, first_child)
            assert seed.calls["third"] == 1 and seed.native(seed.bare, "show-ref") == original_refs

            # Only the operator publishes the document, after the first Run has completed.
            staging = approval_path.with_suffix(".pending")
            staging.write_text(
                json.dumps({"tasks": [first_task.model_dump(), second_task.model_dump()]}),
                encoding="utf-8",
            )
            staging.replace(approval_path)
            runtime.schedules.tick()
            assert current_occurrence() == (first, first_child)
            assert seed.calls["third"] == 1 and seed.native(seed.bare, "show-ref") == original_refs

            staged: list[PreparedBranchAdvance] = []

            def lose_publication_acknowledgement(
                _store: BranchAdvanceStore, preparation: PreparedBranchAdvance, *, observed_tip: str
            ) -> None:
                assert preparation.verification.fleet_run_id == first_id
                assert preparation.verification.checkpoint_id == first_verified["checkpoint_id"]
                assert (
                    observed_tip
                    == preparation.commit
                    == seed.native(seed.bare, "rev-parse", seed.ref)
                )
                staged.append(preparation)
                raise _InterruptedController("lost refill publication acknowledgement")

            with monkeypatch.context() as patch:
                patch.setattr(BranchAdvanceStore, "confirm", lose_publication_acknowledgement)
                with pytest.raises(_InterruptedController, match="lost refill publication"):
                    runtime.schedules.tick()
            (prepared,) = staged
            pending, pending_child = current_occurrence()
            assert pending.schedule_run_id != first.schedule_run_id
            assert pending.status is ScheduleExecutionStatus.RUNNING
            assert pending_child.fleet_run_id is None and seed.calls["third"] == 1
            assert prepared.expected_tip == seed_receipt.preparation.commit
            assert (
                seed.native(seed.bare, "rev-parse", f"{prepared.commit}^1") == prepared.expected_tip
            )
            published_refs = seed.native(seed.bare, "show-ref")
            runtime.shutdown()

            runtime = _writable_runtime(seed)
            runtime.schedules.tick()
            second, second_child = current_occurrence()
            second_id = second_child.fleet_run_id
            assert second.schedule_run_id == pending.schedule_run_id
            assert second_id is not None and second_id != first_id
            assert_fresh_run(second_id, second_task, prepared.commit)
            receipt = branch_advances.find_by_next_run(second_id)
            assert receipt is not None and receipt.published_at is not None
            assert receipt.preparation == prepared

            second_verified = _cli_json(
                seed.cli, seed.checkout, "runs", "verify-candidate", second_id
            )
            assert (
                second_verified["outcome"] == "PASS"
                and second_verified["base_revision"] == prepared.commit
            )
            assert seed.native(seed.checkout, "rev-parse", "HEAD") == prepared.commit
            assert (seed.checkout / "code.py").read_bytes() == _FIRST_WRITABLE_EDIT
            second_events = _run_events(seed.cli, seed.checkout, second_id)
            (second_admission,) = [
                event for event in second_events if event["event_type"] == "RUN_ADMITTED"
            ]
            assert receipt.published_at <= datetime.fromisoformat(second_admission["generated_at"])
            runtime.schedules.tick()
            settled, settled_child = current_occurrence()
            assert (
                settled.status is ScheduleExecutionStatus.COMPLETED
                and settled.finished_at is not None
            )
            assert settled_child.fleet_run_id == second_id
            now = settled.finished_at + timedelta(minutes=2)
            for _ in range(2):
                runtime.schedules.tick()
            assert current_occurrence() == (settled, settled_child)
            history = runtime.runs.history(source=FleetRunSource.SCHEDULED).items
            assert tuple(item.run.fleet_run_id for item in history) == (second_id, first_id)
            assert _run_events(seed.cli, seed.checkout, first_id) == first_events
            assert _run_events(seed.cli, seed.checkout, second_id) == second_events
            assert seed.native(seed.bare, "show-ref") == published_refs
            assert seed.calls == {"a": 1, "b": 1, "third": 2}
            assert not seed.provider.errors, seed.provider.errors

            initial_transcripts = [
                json.dumps(body["messages"])
                for body in seed.provider.requests["third"]
                if body.get("tools")
                and not any(message["role"] == "tool" for message in body["messages"])
            ]
            first_transcript, second_transcript = initial_transcripts
            assert first_prompt in first_transcript and note_body not in first_transcript
            assert second_instruction in second_transcript and first_prompt not in second_transcript
            assert note_body in second_transcript and "UNTRUSTED_AGENT_CONTEXT" in second_transcript
            assert "edited-third-1" not in second_transcript
            assert withheld not in json.dumps(seed.provider.requests["third"])
        finally:
            runtime.shutdown()
