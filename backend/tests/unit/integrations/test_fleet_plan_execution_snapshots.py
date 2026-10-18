from __future__ import annotations

from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepTeardownEvidence,
    FailedPlanStepTeardownEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
)
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
)
from oh_goon.core.models.fleet_plan_step_contract import (
    PlanStepCapabilities,
    PlanStepWorkspace,
    StepWorkspaceSource,
)
from oh_goon.core.models.fleet_plan_values import HostEnvironmentPart, ResolvedEnvironmentBinding
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.path_authority import StrictHostPathAuthority
from oh_goon.core.path_layouts import RunPathLayout, SANDBOX_PATHS
from oh_goon.engine.command import CommandResult
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.engine.git import GitService
import oh_goon.integrations._fleet_plan_runtime_root as runtime_root
from oh_goon.integrations._fleet_plan_resource_evidence import write_remote_resource_evidence
from oh_goon.integrations.content_snapshot import snapshot_directory
from oh_goon.integrations.content_snapshot_dereferenced import snapshot_directory_dereferenced
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import IsolatedProcessRequest
from oh_goon.integrations._fleet_plan_sandbox_launch import _sandbox_environment
from oh_goon.integrations.fleet_plan_run import PreparedPlanRun, _teardown_step_attempts
from tests.factories.runtime_authority import authorized_command_result
from tests.factories.cicd_repository import seed_cicd_repository
from tests.factories.bubblewrap_command import prepared_bubblewrap_command as _prepared
from tests.factories.fleet_plan_executor import (
    TEST_ISOLATION_BACKEND,
    PhysicalFleetPlanExecutorHarness,
    prepare_physical_test_run,
    strict_test_plan,
    source_authority_for_test,
)
from tests.factories.path_authority import TEST_PATH_AUTHORITY


class _UnitProcessIsolation:
    capabilities = IsolationBackendCapabilities(
        backend_id="unit-strict",
        private_filesystem=True,
        private_process_tree=True,
        enforced_network_policy=True,
        content_identified_runtime=True,
        exact_argv=True,
        disposable_attempt=True,
        immutable_authority_after_start=True,
        enforced_cpu_limit=True,
        enforced_memory_limit=True,
        enforced_process_limit=True,
        kernel_boundary=IsolationKernelBoundary.SHARED_HOST_KERNEL,
    )

    def configuration_identity(self) -> str:
        return "process-isolation-backend-unit-strict"

    def require_available(self, plan) -> None:
        return None

    def execute(self, request, *, timeout, cancel_requested, process_observer=None):
        import oh_goon.integrations.fleet_plan_step_execution as step_execution

        assert request.attempt.resources is not None
        write_remote_resource_evidence(request.attempt.paths.root, request.attempt.resources)
        return step_execution.run_command(
            *request.argv,
            timeout=timeout,
            cwd=Path(request.cwd),
            env=dict(request.env),
            cancel_requested=cancel_requested,
        )


def _cicd(*steps: ResolvedCommandStep) -> ResolvedFleetPlan:
    return ResolvedFleetPlan.create(
        name="sandbox-snapshots",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(cpu_millis=2000, memory_mib=4096, max_processes=256),
        ),
        requirements=(),
        steps=steps,
    )


def _executor() -> PhysicalFleetPlanExecutorHarness:
    return PhysicalFleetPlanExecutorHarness(
        GitService(), process_isolation_backends=(_UnitProcessIsolation(),)
    )


def test_repository_source_is_frozen_once_for_the_entire_cicd_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    source = repository / "version.txt"
    source.write_text("v1", encoding="utf-8")
    plan = _cicd(
        ResolvedCommandStep(timeout_seconds=3600, step_id="first", argv=("true", "first")),
        ResolvedCommandStep(timeout_seconds=3600, step_id="second", argv=("true", "second")),
    )
    observed: list[str] = []

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        cwd = Path(kwargs["cwd"])
        observed.append((cwd / "version.txt").read_text(encoding="utf-8"))
        if argv == ("true", "first"):
            source.write_text("v2", encoding="utf-8")
        return authorized_command_result(argv=tuple(argv), returncode=0, stdout="", stderr="")

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    _executor().execute(repository, plan, cancel_requested=None)

    assert observed == ["v1", "v1"]
    assert source.read_text(encoding="utf-8") == "v2"


def test_imported_host_environment_is_captured_once_per_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    monkeypatch.setenv("FLEET_CAPTURED_TOKEN", "first")
    binding = ResolvedEnvironmentBinding(
        name="TOKEN", parts=(HostEnvironmentPart(name="FLEET_CAPTURED_TOKEN"),)
    )
    plan = _cicd(
        ResolvedCommandStep(
            timeout_seconds=3600, step_id="first", argv=("true", "first"), environment=(binding,)
        ),
        ResolvedCommandStep(
            timeout_seconds=3600, step_id="second", argv=("true", "second"), environment=(binding,)
        ),
    )
    observed: list[str] = []

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        observed.append(kwargs["env"]["TOKEN"])
        if argv == ("true", "first"):
            monkeypatch.setenv("FLEET_CAPTURED_TOKEN", "second")
        return authorized_command_result(argv=tuple(argv), returncode=0, stdout="", stderr="")

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    _executor().execute(repository, plan, cancel_requested=None)

    assert observed == ["first", "first"]


def test_pipeline_cannot_bind_arbitrary_readonly_host_path(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    with pytest.raises(ValidationError):
        PlanStepCapabilities(read_only_host_paths=(str(external),))


def test_bound_host_tool_is_frozen_and_live_host_mutation_is_irrelevant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    tool_dir = tmp_path / "bin"
    tool_dir.mkdir()
    tool = tool_dir / "fleet-test-tool"
    tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tool_dir}:{Path('/usr/bin')}:{Path('/bin')}")
    step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="tool",
        argv=("fleet-test-tool",),
        capabilities=PlanStepCapabilities(tools=("sh",)),
    )
    plan = _cicd(step)
    authority = StrictHostPathAuthority.create(
        trusted_roots=(tool_dir, *TEST_PATH_AUTHORITY.trusted_roots),
        tool_search_paths=(tool_dir, *TEST_PATH_AUTHORITY.tool_search_paths),
        runtime_inspector=TEST_PATH_AUTHORITY.runtime_inspector,
    )

    repository_identity = RepositoryIdentity(path=repository)
    with prepare_physical_test_run(
        repository_identity,
        plan,
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=authority,
    ) as session:
        attempt = session.create_attempt(step, 1)
        binding = next(
            item for item in attempt.host_tools if item.name == "fleet-test-tool"
        )
        frozen_before = binding.snapshot_path.read_text(encoding="utf-8")
        tool.write_text("#!/bin/sh\nexit 9\n", encoding="utf-8")
        _prepared(
            BubblewrapProcessIsolation(executable="/usr/bin/true"),
            IsolatedProcessRequest(
                attempt=attempt,
                argv=step.argv,
                cwd=attempt.paths.workspace,
                env={},
            ),
        )
        assert binding.snapshot_path.read_text(encoding="utf-8") == frozen_before
        assert "exit 0" in frozen_before


def test_strict_environment_is_deterministic_not_host_inherited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    monkeypatch.setenv("PATH", "/host/private/bin")
    monkeypatch.setenv("LANG", "sv_SE.UTF-8")
    step = ResolvedCommandStep(timeout_seconds=3600, step_id="strict-env", argv=("true",))

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(step),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as session:
        attempt = session.create_attempt(step, 1)
        prepared = _prepared(
            BubblewrapProcessIsolation(executable="/usr/bin/true"),
            IsolatedProcessRequest(
                attempt=attempt,
                argv=step.argv,
                cwd=attempt.paths.workspace,
                env={"PATH": "/host/private/bin", "LANG": "sv_SE.UTF-8"},
            ),
        )

    assert prepared.env["PATH"] == "/toolchain/bin"
    assert prepared.env["LANG"] == "C"
    assert prepared.env["LC_ALL"] == "C"
    assert prepared.env["UV_NO_MANAGED_PYTHON"] == "1"
    assert prepared.env["UV_PYTHON_DOWNLOADS"] == "never"


def test_strict_environment_binds_uv_to_exact_frozen_system_python() -> None:
    environment = _sandbox_environment(
        {"UV_PYTHON": "/ambient/python"},
        (),
        uv_system_python=PurePosixPath("/usr/bin/python3.12"),
    )

    assert environment["UV_PYTHON"] == "/usr/bin/python3.12"
    assert environment["UV_NO_MANAGED_PYTHON"] == "1"
    assert environment["UV_PYTHON_DOWNLOADS"] == "never"


def test_strict_environment_cannot_reenable_implicit_python_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    monkeypatch.setenv("UV_NO_MANAGED_PYTHON", "0")
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "automatic")
    step = ResolvedCommandStep(timeout_seconds=3600, step_id="strict-uv", argv=("true",))

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(step),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as session:
        attempt = session.create_attempt(step, 1)
        prepared = _prepared(
            BubblewrapProcessIsolation(executable="/usr/bin/true"),
            IsolatedProcessRequest(
                attempt=attempt,
                argv=step.argv,
                cwd=attempt.paths.workspace,
                env={
                    "UV_NO_MANAGED_PYTHON": "0",
                    "UV_PYTHON_DOWNLOADS": "automatic",
                },
            ),
        )

    assert prepared.env["UV_NO_MANAGED_PYTHON"] == "1"
    assert prepared.env["UV_PYTHON_DOWNLOADS"] == "never"


def test_execution_input_evidence_contains_hashes_not_secret_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    (repository / "tracked.txt").write_text("base", encoding="utf-8")
    monkeypatch.setenv("FLEET_SECRET_FOR_EVIDENCE", "do-not-persist")
    binding = ResolvedEnvironmentBinding(
        name="TOKEN", parts=(HostEnvironmentPart(name="FLEET_SECRET_FOR_EVIDENCE"),)
    )
    step = ResolvedCommandStep(
        timeout_seconds=3600, step_id="evidence", argv=("true",), environment=(binding,)
    )
    plan = _cicd(step)
    repository_identity = RepositoryIdentity(path=repository)
    admitted_source = source_authority_for_test(repository_identity)

    with prepare_physical_test_run(
        repository_identity,
        plan,
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
        source_authority=admitted_source,
    ) as session:
        evidence = session.execution_input_evidence()
        assert session.execution_input_evidence() is evidence
        dumped = evidence.model_dump_json()

    assert evidence.source_authority_id == admitted_source.authority_id
    assert evidence.base_workspace.entry_count >= 1
    assert evidence.runtime_root.entry_count >= 1
    assert evidence.host_environment_names == ("FLEET_SECRET_FOR_EVIDENCE",)
    assert "do-not-persist" not in dumped
    assert str(repository) not in dumped


def test_strict_preparation_rejects_source_drift_after_admission(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    authored = repository / "tracked.txt"
    authored.write_text("before\n", encoding="utf-8")
    plan = _cicd(
        ResolvedCommandStep(timeout_seconds=60, step_id="check", argv=("true",))
    )
    repository_identity = RepositoryIdentity(path=repository)
    admitted_source = source_authority_for_test(repository_identity)
    authored.write_text("after\n", encoding="utf-8")
    executor = _executor()

    with pytest.raises(
        FleetPlanInvalid,
        match="does not match admitted repository source authority",
    ):
        executor.prepare_strict_run(
            repository_identity,
            plan,
            cancel_requested=None,
            admitted_source_authority=admitted_source,
            admitted_isolation_backend=executor.admitted_isolation_backend(),
        )


def test_snapshot_cancellation_removes_partial_destination(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for index in range(80):
        (source / f"file-{index:03d}.txt").write_text("x" * 4096, encoding="utf-8")
    destination = tmp_path / "snapshot"
    calls = 0

    def cancel_requested() -> bool:
        nonlocal calls
        calls += 1
        return calls > 35

    with pytest.raises(OperationCancelledError):
        snapshot_directory(
            source,
            destination,
            label="cancelled snapshot",
            cancel_requested=cancel_requested,
        )

    assert not destination.exists()


def test_step_attempts_and_snapshots_are_released_after_last_consumer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    (repository / "base.txt").write_text("base", encoding="utf-8")
    producer = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="producer",
        argv=("true", "producer"),
        workspace=PlanStepWorkspace(publish=True),
    )
    consumer = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="consumer",
        argv=("true", "consumer"),
        workspace=PlanStepWorkspace(source=StepWorkspaceSource(step_id="producer")),
    )
    plan = _cicd(producer, consumer)
    executor = _executor()
    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_step_execution.run_command",
        lambda *argv, **kwargs: authorized_command_result(argv=tuple(argv), returncode=0, stdout="", stderr=""),
    )

    session = executor.prepare_strict_run(
        RepositoryIdentity(path=repository),
        plan,
        cancel_requested=None,
        admitted_isolation_backend=executor.admitted_isolation_backend(),
    )
    assert session is not None
    root = session.root
    base = session.source_for(plan.steps[0])
    try:
        execution = executor.execute_strict_run(repository, plan, session, cancel_requested=None)
        assert all(
            isinstance(step.teardown, CompletedPlanStepTeardownEvidence) for step in execution.steps
        )
        assert [step.teardown.attempt_roots_observed for step in execution.steps] == [1, 1]
        assert not base.exists()
        assert not (root / "attempts").exists() or not any((root / "attempts").iterdir())
        assert not (root / "published" / "producer").exists()
    finally:
        session.close()


def test_execution_input_identity_rejects_tampered_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic import ValidationError

    from oh_goon.core.models.fleet_plan_evidence import PlanExecutionInputEvidence

    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    plan = _cicd(ResolvedCommandStep(timeout_seconds=3600, step_id="evidence", argv=("true",)))
    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        plan,
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as session:
        evidence = session.execution_input_evidence()
    payload = evidence.model_dump(mode="python", exclude_computed_fields=True)
    payload["input_id"] = "plan-execution-input-tampered"

    with pytest.raises(ValidationError):
        PlanExecutionInputEvidence.model_validate(payload)

    changed = PlanExecutionInputEvidence.model_validate(
        evidence.model_dump(mode="python", exclude_computed_fields=True)
        | {"host_environment_names": ("DIFFERENT",)}
    )
    assert changed.input_id != evidence.input_id


def test_managed_runtime_is_snapshotted_before_effect_execution_and_released(
    tmp_path: Path,
) -> None:
    from oh_goon.core.models.fleet_plan import MissingConfigPolicy, ResolvedPreCommitStep
    from oh_goon.integrations.fleet_plan_managed_runtime import PreCommitManagedRuntime

    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    managed = tmp_path / "managed-runtime"
    managed.mkdir()
    payload = managed / "runtime.txt"
    payload.write_text("bound", encoding="utf-8")
    executable = managed / "pre-commit"
    executable.write_text("bound executable", encoding="utf-8")
    step = ResolvedPreCommitStep(
        step_id="managed",
        if_missing=MissingConfigPolicy.SKIP,
        timeout_seconds=30,
        runtime_id="tool-precommit",
        runtime_pyproject_sha256="1" * 64,
        runtime_uv_lock_sha256="2" * 64,
    )
    plan = ResolvedFleetPlan.create(
        name="managed-runtime-snapshot",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(cpu_millis=2000, memory_mib=4096, max_processes=256),
        ),
        requirements=(),
        steps=(step,),
    )

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        plan,
        managed_runtimes={
            step.step_id: PreCommitManagedRuntime(
                environment=managed.resolve(),
                executable=executable.resolve(),
            )
        },
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as session:
        attempt = session.create_attempt(step, 1)
        binding = attempt.sandbox_bindings[0]
        assert binding.sandbox_path == SANDBOX_PATHS.managed_path(0)
        assert str(binding.declared_path) not in str(binding.sandbox_path)
        payload.write_text("mutated-host", encoding="utf-8")
        assert (binding.snapshot_path / "runtime.txt").read_text(encoding="utf-8") == "bound"
        evidence = session.execution_input_evidence()
        assert len(evidence.managed_inputs) == 1
        snapshot_path = binding.snapshot_path
        session.release_step(step)
        assert not snapshot_path.exists()


def test_managed_runtime_kind_must_match_resolved_step(tmp_path: Path) -> None:
    from oh_goon.integrations.fleet_plan_managed_runtime import PreCommitManagedRuntime

    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    managed = tmp_path / "managed-runtime"
    managed.mkdir()
    executable = managed / "pre-commit"
    executable.write_text("tool", encoding="utf-8")
    step = ResolvedCommandStep(timeout_seconds=3600, step_id="command", argv=("true",))
    plan = _cicd(step)

    with pytest.raises(FleetPlanInvalid, match="does not match resolved step kind"):
        prepare_physical_test_run(
            RepositoryIdentity(path=repository),
            plan,
            managed_runtimes={
                step.step_id: PreCommitManagedRuntime(
                    environment=managed.resolve(), executable=executable.resolve()
                )
            },
            isolation_backend=TEST_ISOLATION_BACKEND,
            path_authority=TEST_PATH_AUTHORITY,
        )


def test_dereferenced_snapshot_materializes_external_interpreter_symlink(tmp_path: Path) -> None:
    source = tmp_path / "managed-env"
    bin_dir = source / "bin"
    bin_dir.mkdir(parents=True)
    interpreter = tmp_path / "host-python"
    interpreter.write_bytes(b"ELF-placeholder")
    interpreter.chmod(0o755)
    (bin_dir / "python").symlink_to(interpreter)
    destination = tmp_path / "frozen-env"

    identity = snapshot_directory_dereferenced(source, destination, label="managed runtime test")

    frozen = destination / "bin" / "python"
    assert frozen.is_file()
    assert not frozen.is_symlink()
    assert frozen.read_bytes() == b"ELF-placeholder"
    assert identity.entry_count >= 2


def test_dereferenced_snapshot_identity_matches_copied_tree_when_files_and_directories_interleave(
    tmp_path: Path,
) -> None:
    source = tmp_path / "runtime-tree"
    (source / "a-dir").mkdir(parents=True)
    (source / "a-dir" / "inside").write_text("a", encoding="utf-8")
    (source / "b-file").write_text("b", encoding="utf-8")
    (source / "c-dir").mkdir()
    (source / "c-dir" / "inside").write_text("c", encoding="utf-8")
    destination = tmp_path / "frozen-runtime-tree"

    identity = snapshot_directory_dereferenced(source, destination, label="mixed runtime tree")

    from oh_goon.integrations.content_snapshot import snapshot_identity

    assert snapshot_identity(destination) == identity


def test_dereferenced_snapshot_excludes_root_entry_without_reading_or_hashing_it(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "ca.pem").write_text("public-ca", encoding="utf-8")
    (source / "private").symlink_to(source / "missing-private-target")

    first_destination = tmp_path / "first"
    first = snapshot_directory_dereferenced(
        source,
        first_destination,
        label="runtime with excluded private material",
        excluded_entries=("private",),
    )

    assert (first_destination / "ca.pem").read_text(encoding="utf-8") == "public-ca"
    assert not (first_destination / "private").exists()
    assert not (first_destination / "private").is_symlink()

    (source / "private").unlink()
    (source / "private").mkdir()
    (source / "private" / "host.key").write_text("secret-key", encoding="utf-8")
    second_destination = tmp_path / "second"
    second = snapshot_directory_dereferenced(
        source,
        second_destination,
        label="runtime with changed excluded private material",
        excluded_entries=("private",),
    )

    assert second == first
    assert not (second_destination / "private").exists()


def test_dereferenced_snapshot_exclusion_applies_only_to_source_root(tmp_path: Path) -> None:
    source = tmp_path / "source"
    nested_private = source / "certs" / "private"
    nested_private.mkdir(parents=True)
    (source / "private").mkdir()
    (source / "private" / "host.key").write_text("host-secret", encoding="utf-8")
    (nested_private / "fixture.pem").write_text("nested-public-fixture", encoding="utf-8")

    destination = tmp_path / "destination"
    snapshot_directory_dereferenced(
        source,
        destination,
        label="root-only exclusion",
        excluded_entries=("private",),
    )

    assert not (destination / "private").exists()
    assert (destination / "certs" / "private" / "fixture.pem").read_text(encoding="utf-8") == (
        "nested-public-fixture"
    )


def test_runtime_root_excludes_host_ssl_private_from_network_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[tuple[Path, tuple[str, ...]]] = []

    def snapshot_runtime_directory(
        source: Path,
        destination: Path,
        *,
        label: str,
        cancel_requested,
        excluded_entries: tuple[str, ...] = (),
    ):
        del label, cancel_requested
        observed.append((source, excluded_entries))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.mkdir()
        return None

    monkeypatch.setattr(runtime_root, "_minimal_directories", lambda _paths: (Path("/etc/ssl"),))
    monkeypatch.setattr(runtime_root, "snapshot_directory_dereferenced", snapshot_runtime_directory)

    runtime_root._materialize_sparse_root(
        set(),
        {Path("/etc/ssl")},
        tmp_path / "runtime-root",
        cancel_requested=None,
    )

    assert observed == [(Path("/etc/ssl"), ("private",))]


def test_runtime_dependency_capture_preserves_exact_loader_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "make"
    executable.write_bytes(b"\x7fELF" + b"\x00" * 64)
    executable.chmod(0o755)
    inspector = tmp_path / "ldd"
    inspector.write_text("#!/bin/sh\n", encoding="utf-8")
    inspector.chmod(0o755)
    real = tmp_path / "real" / "ld-linux.so"
    real.parent.mkdir()
    real.write_bytes(b"loader")
    alias = tmp_path / "abi" / "ld-linux.so"
    alias.parent.mkdir()
    alias.symlink_to(real)

    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=f"{alias}\n", stderr="")

    monkeypatch.setattr(runtime_root.subprocess, "run", fake_run)
    aliases: dict[PurePosixPath, Path] = {}
    dependencies = runtime_root._dynamic_dependencies(
        executable, inspector, runtime_aliases=aliases
    )

    assert dependencies == {real.resolve()}
    assert aliases == {PurePosixPath(str(alias)): real.resolve()}


def test_uv_system_python_exact_prepared_path_is_preserved_as_runtime_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.integrations.content_snapshot import SnapshotIdentity
    from oh_goon.integrations.fleet_plan_managed_runtime import UvProjectManagedRuntime

    fake_uv = tmp_path / "uv"
    fake_uv.write_bytes(b"frozen-uv")
    real_python = tmp_path / "python3.13"
    real_python.write_bytes(b"frozen-python")
    selected_python = tmp_path / "python"
    selected_python.symlink_to(real_python.name)

    monkeypatch.setattr(
        runtime_root,
        "find_uv_system_python",
        lambda _uv: (_ for _ in ()).throw(AssertionError("uv Python must not be re-selected")),
    )
    monkeypatch.setattr(runtime_root, "_dynamic_dependencies", lambda *args, **kwargs: set())
    monkeypatch.setattr(runtime_root, "_python_runtime_directories", lambda _python: set())
    monkeypatch.setattr(runtime_root, "_nss_runtime_files", lambda _files: set())
    monkeypatch.setattr(runtime_root, "_materialize_host_tool_runtime_support", lambda *args, **kwargs: None)

    def freeze_file(source: Path, target: Path, **_kwargs: object) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())

    monkeypatch.setattr(runtime_root, "snapshot_host_path", freeze_file)
    monkeypatch.setattr(
        runtime_root,
        "snapshot_identity",
        lambda *_args, **_kwargs: SnapshotIdentity(
            sha256="0" * 64,
            byte_count=0,
            entry_count=0,
        ),
    )

    tool = type(
        "Tool",
        (),
        {
            "name": "uv",
            "source_path": fake_uv,
            "runtime_support": None,
        },
    )()
    destination = tmp_path / "runtime-root"
    binding = runtime_root.capture_runtime_root(
        (tool,),
        {
            "package": UvProjectManagedRuntime(
                project="backend",
                group="build",
                cache=tmp_path / "cache",
                python=selected_python,
            )
        },
        destination,
        runtime_inspector=fake_uv,
        network_enabled=False,
        cancel_requested=None,
    )

    assert binding.uv_system_python == PurePosixPath(str(selected_python))
    exact = destination / selected_python.relative_to("/") if selected_python.is_absolute() else None
    # tmp_path is absolute, so the selected uv-visible path must exist in the frozen root
    # and contain the same immutable bytes as its dereferenced interpreter.
    assert exact is not None
    assert exact.read_bytes() == b"frozen-python"
    resolved = destination / real_python.relative_to("/")
    assert resolved.read_bytes() == b"frozen-python"


def test_runtime_file_alias_preserves_exact_symlink_path_for_resolver_style_files(
    tmp_path: Path,
) -> None:
    target = tmp_path / "generated-resolv.conf"
    target.write_text("nameserver 1.1.1.1\n", encoding="utf-8")
    exact = tmp_path / "resolv.conf"
    exact.symlink_to(target)
    files: set[Path] = set()
    aliases: dict[PurePosixPath, Path] = {}

    runtime_root._include_runtime_file_preserving_exact_path(
        exact, files=files, runtime_aliases=aliases
    )

    assert target.resolve() in files
    assert aliases[PurePosixPath(str(exact))] == target.resolve()


def test_runtime_root_materializes_exact_abi_alias_bytes(tmp_path: Path) -> None:
    source = tmp_path / "loader"
    source.write_bytes(b"frozen-loader")
    destination = tmp_path / "runtime-root"

    runtime_root._materialize_sparse_root(
        set(),
        set(),
        destination,
        file_aliases=((PurePosixPath("/lib64/ld-linux-x86-64.so.2"), source),),
        cancel_requested=None,
    )

    assert (destination / "lib64" / "ld-linux-x86-64.so.2").read_bytes() == b"frozen-loader"


def test_runtime_root_scaffolds_later_readonly_file_and_directory_mounts(tmp_path: Path) -> None:
    destination = tmp_path / "runtime-root"

    runtime_root._materialize_sparse_root(
        set(),
        set(),
        destination,
        directory_mount_targets=(PurePosixPath("/managed/000"),),
        file_mount_targets=(
            PurePosixPath("/etc/passwd"),
            PurePosixPath("/toolchain/bin/uv"),
        ),
        cancel_requested=None,
    )

    assert (destination / "managed" / "000").is_dir()
    assert (destination / "etc" / "passwd").is_file()
    assert (destination / "toolchain" / "bin" / "uv").is_file()


def test_teardown_failure_is_recorded_and_stops_later_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    first = ResolvedCommandStep(timeout_seconds=3600, step_id="first", argv=("true", "first"))
    second = ResolvedCommandStep(timeout_seconds=3600, step_id="second", argv=("true", "second"))
    plan = _cicd(first, second)
    executor = _executor()
    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_step_execution.run_command",
        lambda *argv, **kwargs: authorized_command_result(argv=tuple(argv), returncode=0, stdout="", stderr=""),
    )
    original = PreparedPlanRun.release_step
    calls = 0

    def release(session, step):
        nonlocal calls
        calls += 1
        completed = original(session, step)
        if calls == 1:
            return FailedPlanStepTeardownEvidence(
                attempt_roots_observed=completed.attempt_roots_observed,
                reason_code="ROOT_REMAINS",
            )
        return completed

    monkeypatch.setattr(PreparedPlanRun, "release_step", release)
    session = executor.prepare_strict_run(
        RepositoryIdentity(path=repository),
        plan,
        cancel_requested=None,
        admitted_isolation_backend=executor.admitted_isolation_backend(),
    )
    assert session is not None
    try:
        execution = executor.execute_strict_run(repository, plan, session, cancel_requested=None)
    finally:
        session.close()

    assert isinstance(execution.steps[0].teardown, FailedPlanStepTeardownEvidence)
    assert execution.steps[1].status == "SKIPPED"
    assert isinstance(execution.steps[1].teardown, CompletedPlanStepTeardownEvidence)
    assert execution.steps[1].teardown.attempt_roots_observed == 0


def test_python_runtime_native_dependencies_freeze_stdlib_extension_shared_library_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stdlib = tmp_path / "usr" / "lib" / "python3.12"
    extension = stdlib / "lib-dynload" / "_ssl.cpython-312-x86_64-linux-gnu.so"
    extension.parent.mkdir(parents=True)
    extension.write_bytes(b"\x7fELF" + b"\x00" * 64)
    extension.chmod(0o755)
    inspector = tmp_path / "ldd"
    inspector.write_text("#!/bin/sh\n", encoding="utf-8")
    inspector.chmod(0o755)
    libssl_real = tmp_path / "usr" / "lib" / "x86_64-linux-gnu" / "libssl.so.3"
    libssl_real.parent.mkdir(parents=True)
    libssl_real.write_bytes(b"ssl")
    libssl_alias = tmp_path / "lib" / "x86_64-linux-gnu" / "libssl.so.3"
    libssl_alias.parent.mkdir(parents=True)
    libssl_alias.symlink_to(libssl_real)

    def fake_run(argv, **_kwargs):
        assert argv == (str(inspector), str(extension.resolve()))
        return SimpleNamespace(
            returncode=0,
            stdout=f"libssl.so.3 => {libssl_alias} (0x1234)\n",
            stderr="",
        )

    monkeypatch.setattr(runtime_root.subprocess, "run", fake_run)
    aliases: dict[PurePosixPath, Path] = {}
    dependencies = runtime_root._python_runtime_native_dependencies(
        {stdlib}, inspector, runtime_aliases=aliases
    )

    assert dependencies == {libssl_real.resolve()}
    assert aliases[PurePosixPath(str(libssl_alias))] == libssl_real.resolve()


def test_python_runtime_native_dependencies_ignore_non_native_stdlib_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stdlib = tmp_path / "python3.12"
    stdlib.mkdir()
    (stdlib / "ssl.py").write_text("# pure Python\n", encoding="utf-8")
    calls: list[object] = []
    monkeypatch.setattr(runtime_root.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    dependencies = runtime_root._python_runtime_native_dependencies(
        {stdlib}, tmp_path / "ldd", runtime_aliases={}
    )

    assert dependencies == set()
    assert calls == []


def test_private_attempt_teardown_handles_read_only_materialized_directories(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    layout = RunPathLayout.from_root(run_root)
    attempt = layout.attempt_root("dependency-materialize", 1)
    readonly = attempt / "workspace" / ".venv" / "lib"
    readonly.mkdir(parents=True)
    (readonly / "payload.bin").write_bytes(b"sealed")
    readonly.chmod(0o500)
    (readonly.parent).chmod(0o500)

    result = _teardown_step_attempts(layout, "dependency-materialize")

    assert isinstance(result, CompletedPlanStepTeardownEvidence)
    assert result.attempt_roots_observed == 1
    assert not layout.step_attempts_root("dependency-materialize").exists()
