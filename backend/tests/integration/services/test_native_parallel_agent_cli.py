"""The public parallel CLI admits original Pi Runs and reopens their evidence."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.models.agent_providers import AgentProvidersConfig
from oh_goon.core.models.branch_advance import PreparedBranchAdvance
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.parallel_agent_work import ParallelAgentWorkRequest
from oh_goon.core.models.parallel_candidate_verification import (
    FrozenVerifierInput,
    ParallelCandidateVerifier,
)
from oh_goon.core.models.run_resources import RunConcurrencyPolicy
from oh_goon.engine.command import run_command
from oh_goon.engine.git import GitService
from oh_goon.runtime.branch_advances import BranchAdvanceStore
from oh_goon.runtime.database import LocalDatabase
from oh_goon.services.branch_advance import BranchAdvanceRequest, advance_verified_candidate
from tests.factories.native_run_concurrency import selected_native_concurrency_environment
from tests.factories.native_two_run import (
    PAIR_VERIFICATION_ARGV,
    NativeTwoRunProvider,
    native_two_run_provider,
)

_POLICY_ENV = "OH_GOON_NATIVE_PARALLEL_CLI_POLICY"
_HOST_ENV = ("PATH", "LANG", "LC_ALL", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR")
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


@pytest.mark.certification
@pytest.mark.no_resume
def test_installed_writable_rounds_publish_before_original_successor_admission(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected_pi_environment(request, timeout=360)
    if selected is None:
        return
    policy, program, runtime_root = selected
    cli = Path(sys.executable).with_name("oh-goon")
    assert cli.is_file()
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(tmp_path / "league.yml"))
    monkeypatch.setenv("HASHMARKS_NO_UPDATE_CHECK", "1")

    original_source = b"# Original repository a\nVALUE = 41\n"
    accepted_seed = b"VALUE = 42\n"
    first_edit = b"VALUE = 42\n# First verified round\n"
    second_edit = b"VALUE = 42\n# Second verified round\n"
    edits = {
        "a": ((original_source, accepted_seed),),
        "b": ((original_source, b"VALUE = 43\n"),),
        "third": ((accepted_seed, first_edit), (first_edit, second_edit)),
    }
    calls = {label: 0 for label in edits}

    def model_turn(label: str, body: dict[str, Any]) -> tuple[dict[str, Any], str]:
        tools_returned = [item["content"] for item in body["messages"] if item["role"] == "tool"]
        if tools_returned:
            assert f"edited-{label}-{calls[label]}" in json.dumps(tools_returned)
            return {"role": "assistant", "content": "The candidate edit is ready."}, "stop"

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
        return {
            "role": "assistant",
            "tool_calls": [
                {
                    "index": 0,
                    "id": f"edit_{label}_{calls[label]}",
                    "type": "function",
                    "function": {"name": tool["name"], "arguments": json.dumps(arguments)},
                }
            ],
        }, "tool_calls"

    with native_two_run_provider(
        tmp_path,
        policy,
        program=program,
        public_runtime=runtime_root,
        same_repository=True,
        model_turn=model_turn,
    ) as provider:
        repository = provider.repositories["a"]
        git = GitService()

        def native(path: Path, *arguments: str) -> str:
            result = git.run(*arguments, cwd=path)
            assert result.returncode == 0, (arguments, result.stderr)
            return result.stdout

        original = native(repository, "rev-parse", "HEAD")
        original_refs = native(repository, "show-ref", "--head")
        bare = tmp_path / "branch-owner.git"
        checkout = tmp_path / "receiving"
        ref = "refs/heads/qualification/writable"
        native(tmp_path, "clone", "--bare", "--local", "--no-hardlinks", str(repository), str(bare))
        native(bare, "update-ref", ref, original)
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
            work_id="native-writable-seed",
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
            notes_enabled=False,
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
            zip((accepted_seed, first_edit), (first_edit, second_edit), strict=True), start=1
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
        (Path(candidate["path"]) / "code.py").write_bytes(second_edit + b"# Unverified change\n")
        final_refs = native(bare, "show-ref")
        stale = _cli_json(
            cli, checkout, *advance_args("changed-candidate", accepted, tip), expected_status=2
        )
        assert stale["status"] == "rejected"
        assert native(bare, "show-ref") == final_refs
        assert native(repository, "show-ref", "--head") == original_refs
        assert (repository / "code.py").read_bytes() == original_source
        assert calls == {"a": 1, "b": 1, "third": 2}
        assert not provider.errors, provider.errors
