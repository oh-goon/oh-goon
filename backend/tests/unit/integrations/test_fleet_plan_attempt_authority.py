from __future__ import annotations

from dataclasses import fields
from types import SimpleNamespace
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
)
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
)
from oh_goon.core.models.fleet_plan_step_contract import PlanStepCapabilities
from oh_goon.core.models.fleet_plan_values import HostEnvironmentPart, ResolvedEnvironmentBinding
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.path_authority import RepositoryPathSpace
from oh_goon.engine.git import GitService
from oh_goon.integrations.content_snapshot import snapshot_identity
from oh_goon.integrations.fleet_plan_process_isolation import IsolatedProcessRequest
from oh_goon.integrations.fleet_plan_run import (
    FleetPlanStepAttempt,
    _materialize_generated_runtime_links,
)
from oh_goon.integrations.fleet_plan_run_execution import (
    FleetPlanRunExecutor as ProductionFleetPlanRunExecutor,
)
from tests.factories.cicd_repository import seed_cicd_repository
from tests.factories.fleet_plan_executor import (
    TEST_ISOLATION_BACKEND,
    PhysicalFleetPlanExecutorHarness,
    prepare_physical_test_run,
    source_authority_for_test,
    strict_test_plan,
)
from tests.factories.path_authority import TEST_PATH_AUTHORITY


def _stub_runtime_snapshot_file(monkeypatch: pytest.MonkeyPatch) -> None:
    def copy_file(source: Path, destination: Path, **_kwargs: object):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        destination.chmod(source.stat().st_mode & 0o777)
        return SimpleNamespace()

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_run.snapshot_file", copy_file)


class _PreparationOnlyIsolation:
    capabilities = IsolationBackendCapabilities(
        backend_id="preparation-only",
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
        return "process-isolation-preparation-only"

    def require_available(self, plan: ResolvedFleetPlan) -> None:
        del plan

    def execute(self, *args, **kwargs):  # pragma: no cover - preparation must not execute
        raise AssertionError("preparation-only backend executed")


def _strict_plan(argv: tuple[str, ...] = ("true",)) -> ResolvedFleetPlan:
    return ResolvedFleetPlan.create(
        name="attempt-authority",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(cpu_millis=2000, memory_mib=4096, max_processes=256),
        ),
        requirements=(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="strict",
                argv=argv,
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )


def test_isolated_process_request_cannot_duplicate_attempt_authority() -> None:
    request_fields = {field.name for field in fields(IsolatedProcessRequest)}
    assert "attempt" in request_fields
    assert request_fields - {"attempt"} == {
        "argv",
        "cwd",
        "env",
        "read_only_workspace",
        "termination_requested",
        # B12C: per-command identity is transport, not a new placement/admission owner.
        "fleet_run_id",
        "step_id",
        "command_ordinal",
        "local_resource_intent",
    }
    assert not request_fields.intersection(
        {
            "executor_id",
            "executor_generation",
            "executor_trust_epoch",
            "isolation_backend",
            "path_authority",
            "host_tools",
            "execution_authority",
        }
    )


def test_strict_prepare_freezes_managed_runtimes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    calls = 0

    def prepare_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {}

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_run_execution.prepare_managed_runtimes",
        prepare_once,
    )
    executor = PhysicalFleetPlanExecutorHarness(
        GitService(), process_isolation_backends=(_PreparationOnlyIsolation(),)
    )
    prepared = executor.prepare_strict_run(
        RepositoryIdentity(path=repository),
        _strict_plan(),
        cancel_requested=None,
        admitted_isolation_backend=executor.admitted_isolation_backend(),
    )

    assert prepared is not None
    prepared.close()
    assert calls == 1


def test_strict_preparation_requires_backend_admitted_before_run(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    backend = _PreparationOnlyIsolation()
    executor = ProductionFleetPlanRunExecutor(
        GitService(),
        process_isolation_backends=(backend,),
        path_authority=TEST_PATH_AUTHORITY,
    )

    with pytest.raises(FleetPlanInvalid, match="admitted before RUN_ADMITTED"):
        repository_identity = RepositoryIdentity(path=repository)
        executor.prepare_strict_run(
            repository_identity,
            _strict_plan(),
            cancel_requested=None,
            admitted_isolation_backend=None,
            admitted_source_authority=source_authority_for_test(repository_identity),
        )


def test_direct_preparation_rejects_strict_plan(tmp_path: Path) -> None:
    with pytest.raises(FleetPlanInvalid, match="Strict CI/CD plans require prepare_strict_run"):
        PhysicalFleetPlanExecutorHarness(GitService()).prepare_direct_run(
            RepositoryIdentity(path=tmp_path),
            _strict_plan(),
            cancel_requested=None,
        )


def test_prepared_strict_run_rejects_plan_identity_drift(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    executor = PhysicalFleetPlanExecutorHarness(
        GitService(), process_isolation_backends=(_PreparationOnlyIsolation(),)
    )
    prepared = executor.prepare_strict_run(
        RepositoryIdentity(path=repository),
        _strict_plan(),
        cancel_requested=None,
        admitted_isolation_backend=executor.admitted_isolation_backend(),
    )
    try:
        with pytest.raises(FleetPlanInvalid, match="Prepared strict run is bound to plan"):
            executor.execute_strict_run(
                repository,
                _strict_plan(("false",)),
                prepared,
                cancel_requested=None,
            )
    finally:
        prepared.close()


def test_physical_step_attempt_does_not_duplicate_run_or_backend_authority(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(timeout_seconds=3600, step_id="bound", argv=("true",))

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository), strict_test_plan(step)
    ) as run:
        attempt = run.create_attempt(step, 1)
        assert attempt.paths.workspace.is_dir()
        assert not hasattr(attempt, "run")
        assert not hasattr(attempt, "process_isolation")
        assert not hasattr(attempt, "attempt")
        assert not hasattr(run, "context")


def test_read_only_source_observation_does_not_rehash_frozen_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(timeout_seconds=3600, step_id="observe", argv=("true",))

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(step),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as context:
        monkeypatch.setattr(
            "oh_goon.integrations.fleet_plan_run.verify_snapshot",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("read-only source observation must not own mutation proof")
            ),
            raising=False,
        )
        assert context.source_for(step).is_dir()


def test_attempt_creation_hashes_only_the_copied_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(timeout_seconds=3600, step_id="copy-once", argv=("true",))

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(step),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as context:
        source = context.source_for(step)
        observed_paths: list[Path] = []
        from oh_goon.integrations import content_snapshot

        real_snapshot_identity = content_snapshot.snapshot_identity

        def record_snapshot_identity(path: Path, **kwargs):
            resolved = Path(path).resolve()
            observed_paths.append(resolved)
            assert resolved != source.resolve(), (
                "frozen source must not be re-hashed during attempt copy"
            )
            return real_snapshot_identity(path, **kwargs)

        monkeypatch.setattr(content_snapshot, "snapshot_identity", record_snapshot_identity)
        attempt = context.create_attempt(step, 1)

        assert observed_paths == [attempt.paths.workspace.resolve()]



def test_attempt_copy_identity_still_rejects_frozen_source_drift(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(timeout_seconds=3600, step_id="copy", argv=("true",))

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(step),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as context:
        source = context.source_for(step)
        (source / "pyproject.toml").write_text("[project]\nname='drifted'\n", encoding="utf-8")
        with pytest.raises(FleetPlanInvalid, match="source changed while creating attempt"):
            context.create_attempt(step, 1)


def test_attempt_mutation_cannot_change_frozen_source_or_next_attempt(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    tracked = repository / "authority.txt"
    tracked.write_text("original\n", encoding="utf-8")
    step = ResolvedCommandStep(timeout_seconds=3600, step_id="isolated-copy", argv=("true",))

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(step),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as context:
        source = context.source_for(step)
        source_file = source / "authority.txt"
        original_bytes = source_file.read_bytes()
        original_identity = snapshot_identity(source)

        first = context.create_attempt(step, 1)
        (first.paths.workspace / "authority.txt").write_text(
            "attempt-mutated\n", encoding="utf-8"
        )

        assert source_file.read_bytes() == original_bytes
        assert snapshot_identity(source) == original_identity

        second = context.create_attempt(step, 2)
        assert (second.paths.workspace / "authority.txt").read_bytes() == original_bytes


def test_strict_attempt_cannot_bind_step_added_after_run_preparation(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    admitted = ResolvedCommandStep(timeout_seconds=3600, step_id="admitted", argv=("true",))
    unbound = ResolvedCommandStep(timeout_seconds=3600, step_id="unbound", argv=("true",))

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(admitted),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as context:
        with pytest.raises(FleetPlanInvalid, match="was not bound during strict run preparation"):
            context.create_attempt(unbound, 1)

def test_attempt_exposes_only_the_current_steps_frozen_host_tools(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    shell_step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="shell-owner",
        argv=("true",),
        capabilities=PlanStepCapabilities(tools=("sh",)),
    )
    plain_step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="plain",
        argv=("true",),
    )

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(shell_step, plain_step),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as context:
        shell_attempt = context.create_attempt(shell_step, 1)
        plain_attempt = context.create_attempt(plain_step, 1)

        shell_names = tuple(binding.name for binding in shell_attempt.host_tools)
        plain_names = tuple(binding.name for binding in plain_attempt.host_tools)

        from oh_goon.core.fleet_plan_tool_requirements import (
            SHELL_RUNTIME_SUPPORT_TOOLS,
        )

        assert set(shell_names) == {"sh", "true", *SHELL_RUNTIME_SUPPORT_TOOLS}
        assert plain_names == ("true",)
        assert (set(SHELL_RUNTIME_SUPPORT_TOOLS) - {"true"}).isdisjoint(plain_names)


def test_attempt_does_not_inherit_unrelated_steps_host_tools(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    shell_step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="shell-owner",
        argv=("true",),
        capabilities=PlanStepCapabilities(tools=("sh",)),
    )
    git_step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="git-owner",
        argv=("git", "status", "--short"),
    )

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(shell_step, git_step),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as context:
        shell_attempt = context.create_attempt(shell_step, 1)
        git_attempt = context.create_attempt(git_step, 1)

        shell_names = {binding.name for binding in shell_attempt.host_tools}
        git_names = {binding.name for binding in git_attempt.host_tools}

        assert "git" not in shell_names
        assert git_names == {"git"}
        assert "sh" not in git_names



def test_attempt_exposes_only_the_current_steps_imported_host_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    monkeypatch.setenv("FLEET_STEP_A_SECRET", "secret-a")
    monkeypatch.setenv("FLEET_STEP_B_SECRET", "secret-b")
    first = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="first",
        argv=("true",),
        environment=(
            ResolvedEnvironmentBinding(
                name="TOKEN",
                parts=(HostEnvironmentPart(name="FLEET_STEP_A_SECRET"),),
            ),
        ),
    )
    second = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="second",
        argv=("true",),
        environment=(
            ResolvedEnvironmentBinding(
                name="TOKEN",
                parts=(HostEnvironmentPart(name="FLEET_STEP_B_SECRET"),),
            ),
        ),
    )

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(first, second),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as context:
        first_attempt = context.create_attempt(first, 1)
        second_attempt = context.create_attempt(second, 1)

        assert dict(first_attempt.host_environment) == {"FLEET_STEP_A_SECRET": "secret-a"}
        assert dict(second_attempt.host_environment) == {"FLEET_STEP_B_SECRET": "secret-b"}
        assert set(context.host_environment) == {"FLEET_STEP_A_SECRET", "FLEET_STEP_B_SECRET"}


def test_generated_backend_venv_runtime_link_is_materialized_from_frozen_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_runtime_snapshot_file(monkeypatch)
    workspace = tmp_path / "workspace"
    cache = tmp_path / "cache"
    runtime_root = tmp_path / "runtime-root"
    python = runtime_root / "usr/bin/python3"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"frozen-python")
    python.chmod(0o755)
    link = workspace / "backend/.venv/bin/python3"
    link.parent.mkdir(parents=True)
    cache.mkdir()
    try:
        link.symlink_to("/usr/bin/python3")
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    attempt = SimpleNamespace(
        paths=SimpleNamespace(workspace=workspace, cache=cache),
        runtime_root=SimpleNamespace(root=runtime_root),
        host_tools=(),
        sandbox_bindings=(),
    )

    _materialize_generated_runtime_links(attempt)  # type: ignore[arg-type]

    assert link.is_file()
    assert not link.is_symlink()
    assert link.read_bytes() == b"frozen-python"
    RepositoryPathSpace.from_root(workspace).require_owned_tree()


def test_generated_root_venv_runtime_link_is_materialized_from_frozen_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_runtime_snapshot_file(monkeypatch)
    workspace = tmp_path / "workspace"
    cache = tmp_path / "cache"
    runtime_root = tmp_path / "runtime-root"
    python = runtime_root / "usr/bin/python3"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"frozen-python")
    python.chmod(0o755)
    link = workspace / ".venv/bin/python3"
    link.parent.mkdir(parents=True)
    cache.mkdir()
    try:
        link.symlink_to("/usr/bin/python3")
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    attempt = SimpleNamespace(
        paths=SimpleNamespace(workspace=workspace, cache=cache),
        runtime_root=SimpleNamespace(root=runtime_root),
        host_tools=(),
        sandbox_bindings=(),
    )

    _materialize_generated_runtime_links(attempt)  # type: ignore[arg-type]

    assert link.is_file()
    assert not link.is_symlink()
    assert link.read_bytes() == b"frozen-python"
    RepositoryPathSpace.from_root(workspace).require_owned_tree()


def test_generated_uv_project_environment_runtime_link_is_materialized_from_frozen_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_runtime_snapshot_file(monkeypatch)
    workspace = tmp_path / "workspace"
    cache = tmp_path / "cache"
    runtime_root = tmp_path / "runtime-root"
    workspace.mkdir()
    cache.mkdir()
    python = runtime_root / "usr/bin/python3"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"frozen-python")
    python.chmod(0o755)
    link = cache / "uv-project-environment/bin/python3"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to("/usr/bin/python3")
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    attempt = SimpleNamespace(
        paths=SimpleNamespace(workspace=workspace, cache=cache),
        runtime_root=SimpleNamespace(root=runtime_root),
        host_tools=(),
        sandbox_bindings=(),
    )

    _materialize_generated_runtime_links(attempt)  # type: ignore[arg-type]

    assert link.is_file()
    assert not link.is_symlink()
    assert link.read_bytes() == b"frozen-python"
    RepositoryPathSpace.from_root(cache).require_owned_tree()


def test_generated_uv_project_environment_relative_interpreter_chain_is_materialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_runtime_snapshot_file(monkeypatch)
    workspace = tmp_path / "workspace"
    cache = tmp_path / "cache"
    runtime_root = tmp_path / "runtime-root"
    workspace.mkdir()
    cache.mkdir()
    python = runtime_root / "usr/bin/python3"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"frozen-python")
    python.chmod(0o755)
    bin_dir = cache / "uv-project-environment/bin"
    bin_dir.mkdir(parents=True)
    python_link = bin_dir / "python"
    python3_link = bin_dir / "python3"
    try:
        python_link.symlink_to("python3")
        python3_link.symlink_to("/usr/bin/python3")
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    attempt = SimpleNamespace(
        paths=SimpleNamespace(workspace=workspace, cache=cache),
        runtime_root=SimpleNamespace(root=runtime_root),
        host_tools=(),
        sandbox_bindings=(),
    )

    _materialize_generated_runtime_links(attempt)  # type: ignore[arg-type]

    assert python_link.read_bytes() == b"frozen-python"
    assert python3_link.read_bytes() == b"frozen-python"
    assert not python_link.is_symlink()
    assert not python3_link.is_symlink()
    RepositoryPathSpace.from_root(cache).require_owned_tree()


def test_seal_owned_outputs_materializes_generated_uv_environment_before_cache_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_runtime_snapshot_file(monkeypatch)
    attempt_root = tmp_path / "attempt"
    workspace = attempt_root / "workspace"
    cache = attempt_root / "cache"
    runtime_root = tmp_path / "runtime-root"
    workspace.mkdir(parents=True)
    cache.mkdir()
    python = runtime_root / "usr/bin/python3"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"frozen-python")
    python.chmod(0o755)
    link = cache / "uv-project-environment/bin/python3"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to("/usr/bin/python3")
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    authority = object()
    attempt = SimpleNamespace(
        paths=SimpleNamespace(root=attempt_root, workspace=workspace, cache=cache),
        runtime_root=SimpleNamespace(root=runtime_root),
        host_tools=(),
        sandbox_bindings=(),
        _output_publication_authority=authority,
    )

    sealed = FleetPlanStepAttempt.seal_owned_outputs(attempt)  # type: ignore[arg-type]

    assert not link.is_symlink()
    assert link.read_bytes() == b"frozen-python"
    assert sealed.cache == cache.resolve()
    assert sealed.workspace == workspace.resolve()
    assert sealed._authority is authority


def test_generated_uv_project_environment_cannot_materialize_mutable_attempt_authority(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    cache = tmp_path / "cache"
    runtime_root = tmp_path / "runtime-root"
    workspace.mkdir()
    cache.mkdir()
    runtime_root.mkdir()
    link = cache / "uv-project-environment/bin/python3"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to("/workspace/python3")
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    attempt = SimpleNamespace(
        paths=SimpleNamespace(workspace=workspace, cache=cache),
        runtime_root=SimpleNamespace(root=runtime_root),
        host_tools=(),
        sandbox_bindings=(),
    )

    with pytest.raises(FleetPlanInvalid, match="declared frozen sandbox authority"):
        _materialize_generated_runtime_links(attempt)  # type: ignore[arg-type]


def test_non_generated_cache_escape_is_never_sealed_or_ignored(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    cache = tmp_path / "cache"
    runtime_root = tmp_path / "runtime-root"
    workspace.mkdir()
    cache.mkdir()
    runtime_root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("secret", encoding="utf-8")
    escape = cache / "other-link"
    try:
        escape.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    attempt = SimpleNamespace(
        paths=SimpleNamespace(workspace=workspace, cache=cache),
        runtime_root=SimpleNamespace(root=runtime_root),
        host_tools=(),
        sandbox_bindings=(),
    )

    _materialize_generated_runtime_links(attempt)  # type: ignore[arg-type]

    with pytest.raises(FleetPlanInvalid, match="escaping symlink"):
        RepositoryPathSpace.from_root(cache).require_owned_tree()


def test_generated_runtime_link_cannot_materialize_mutable_attempt_authority(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    cache = tmp_path / "cache"
    runtime_root = tmp_path / "runtime-root"
    runtime_root.mkdir()
    link = workspace / "backend/.venv/bin/python3"
    link.parent.mkdir(parents=True)
    cache.mkdir()
    try:
        link.symlink_to("/cache/python3")
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    attempt = SimpleNamespace(
        paths=SimpleNamespace(workspace=workspace, cache=cache),
        runtime_root=SimpleNamespace(root=runtime_root),
        host_tools=(),
        sandbox_bindings=(),
    )

    with pytest.raises(FleetPlanInvalid, match="declared frozen sandbox authority"):
        _materialize_generated_runtime_links(attempt)  # type: ignore[arg-type]


def test_non_generated_workspace_escape_is_never_sealed_or_ignored(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    cache = tmp_path / "cache"
    runtime_root = tmp_path / "runtime-root"
    workspace.mkdir()
    cache.mkdir()
    runtime_root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("secret", encoding="utf-8")
    escape = workspace / "source-link"
    try:
        escape.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    attempt = SimpleNamespace(
        paths=SimpleNamespace(workspace=workspace, cache=cache),
        runtime_root=SimpleNamespace(root=runtime_root),
        host_tools=(),
        sandbox_bindings=(),
    )

    _materialize_generated_runtime_links(attempt)  # type: ignore[arg-type]

    with pytest.raises(FleetPlanInvalid, match="escaping symlink"):
        RepositoryPathSpace.from_root(workspace).require_owned_tree()


def test_attempt_number_cannot_recreate_physical_attempt_root(tmp_path: Path) -> None:
    """A reproducible attempt number identifies a slot; it must not authorize replacement."""

    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(timeout_seconds=3600, step_id="attempt-slot", argv=("true",))

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(step),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as context:
        first = context.create_attempt(step, 1)
        sealed = first.seal_owned_outputs()

        with pytest.raises(FleetPlanInvalid, match="physical attempt already exists"):
            context.create_attempt(step, 1)

        # Failed recreation must leave the original physical resource and its
        # already-minted publication authority intact.
        assert first.paths.root.is_dir()
        sealed.prove_for(first)
