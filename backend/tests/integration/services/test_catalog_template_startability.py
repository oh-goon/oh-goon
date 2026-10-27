from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path, PurePosixPath

import pytest

from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_tool_requirements import required_runtime_tool_names
from oh_goon.core.models.fleet_plan import (
    ResolvedCommandStep,
    ResolvedFleetPlan,
    ResolvedOciBuildStep,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import fleet_repository_scope
from oh_goon.core.path_authority import StrictHostPathAuthority
from oh_goon.core.path_layouts import ManagedPreparationPathLayout, RunPathLayout
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunSource,
    FleetRunStatus,
    OperationAction,
)
from oh_goon.engine.native_uv_environment import capture_native_uv_authority
from oh_goon.integrations.executable_materialization_authority import (
    logical_step_execution_authority,
)
from oh_goon.integrations.fleet_plan_environment import step_host_environment_names
from oh_goon.integrations.python_goon_projects import materialize_plan_projects
from oh_goon.integrations.uv_python_runtime import find_uv_system_python
from oh_goon.runtime.plan_templates import PlanTemplateStore
from tests.factories.shipped_templates import agent_plan_template_keys
from tests.integration.services.test_shipped_template_startability import (
    _admission_stack,
    _install_source_identity_observer,
    _materialized_executable,
    _repository_bytes,
    _reviewed_shipped_target,
)

ROOT = Path(__file__).resolve().parents[4]
TEMPLATES = ROOT / "templates"
CERTIFICATION = ROOT / "template-certification"
FIXTURES = CERTIFICATION / "fixtures"
CATALOG = CERTIFICATION / "catalog.json"
KUBERNETES_CERTIFICATION_TOKEN = "token-0123456789-abcd"
KUBERNETES_CERTIFICATION_CA = """-----BEGIN CERTIFICATE-----
MIICwjCCAaqgAwIBAgIBATANBgkqhkiG9w0BAQsFADAaMRgwFgYDVQQDDA9vaC1n
b29uLXRlc3QtY2EwHhcNMjYwODE0MTg1MDMyWhcNMjcwODE1MTg1MDMyWjAaMRgw
FgYDVQQDDA9vaC1nb29uLXRlc3QtY2EwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAw
ggEKAoIBAQDALl/pha1xZR49druzrPUqEopFYmA0NmuLB2zs2nvDEk7Ck+KA23Qx
f0bXTQJdU4SPiXF5wzFfbrVjzHFb8fFF2zCHnknucWVDCyBe/bXQwvlX408Dbm0Q
nNr8aQwv/D65Ub1E1ptEwj+oKomgsYXl0Sxo60I2waWupjDVSJmMwZ9SVlnY0GP/
pkdei9VfIy+oGiJqZVE03fKbjJHix28//TQ0kvmpqlShF9W50uAf7HeNCKtR8r7J
hW6RS6SBd2V+swvFmLGg3WJbZFvlzd2gwZHm7AY/sYawyCWQ8cT51ST8X3S8mDT8
9d4jj1czfZxkf49m9xvOuqRVypXciTr/AgMBAAGjEzARMA8GA1UdEwEB/wQFMAMB
Af8wDQYJKoZIhvcNAQELBQADggEBACyO/q15gh0lqVviLOUD2+rcSHNtytfaOKZm
OdD+w7CHECJceIjvfcnmkw8L97WjKFTBDHJrMX3mcRWYyZN8cU03ZGN9bToHFJug
wKcR4K2vYYEjU/GDxxkPIHkTEV3Kwqm0fFmkZDkxg3BUcPyAweicLyeyXv58FAek
wxKaCn7bbelI9EVHpac+0CNgswyOK47VL0Hn2UxPZNTHe9ZYcI2uaSDhUr9gOyCw
XpaBIU6p7MUOv3YFtfJdJn4OgzdMgu+niAbAjp+hyXxFlwoT1hp3cxBTb/3eNw4k
jgvikjr7NWNBdWEzMRvC202eHuYoVEpufr8IbJAzm6B/ETrQ0Dk=
-----END CERTIFICATE-----
"""


def _catalog_scenarios() -> dict[str, dict[str, object]]:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    plans = payload["plans"]
    assert isinstance(plans, dict)
    return plans


SCENARIOS = _catalog_scenarios()


def _copy_fixture(fixture_key: str, repository: Path) -> None:
    fixture = FIXTURES.joinpath(*PurePosixPath(fixture_key).parts)
    assert fixture.is_dir(), fixture_key
    shutil.copytree(fixture, repository)
    (repository / ".git").mkdir()


def _fixture_path_authority(plan: ResolvedFleetPlan, root: Path) -> StrictHostPathAuthority:
    toolchain = root / "native-toolchain"
    toolchain.mkdir(parents=True)
    runtime_inspector = Path(shutil.which("ldd") or "")
    fallback = Path(shutil.which("true") or "")
    if not runtime_inspector.is_file() or not fallback.is_file():
        raise RuntimeError("template certification requires host ldd and true")

    for name in required_runtime_tool_names(plan):
        source = Path(shutil.which(name) or fallback)
        entry = toolchain / name
        if not entry.exists():
            entry.symlink_to(source.resolve())

    return StrictHostPathAuthority.create(
        trusted_roots=(toolchain, runtime_inspector.resolve().parent),
        tool_search_paths=(toolchain,),
        runtime_inspector=runtime_inspector.resolve(),
    )


def _native_uv_environment(environment: Path, cache: Path, python: Path) -> dict[str, str]:
    environment.parent.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(
        {
            "UV_PROJECT_ENVIRONMENT": str(environment),
            "UV_CACHE_DIR": str(cache),
            "UV_NO_MANAGED_PYTHON": "1",
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_PYTHON": str(python),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return env


def _run_uv(
    uv: Path,
    argv: tuple[str, ...],
    *,
    cwd: Path,
    env: dict[str, str],
    failure_prefix: str,
) -> None:
    result = subprocess.run(
        (str(uv), *argv),
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "native uv failed").strip()
        raise FleetPlanInvalid(f"{failure_prefix}: {detail}")


def _materialize_fixture_environment(
    uv: Path,
    argv: tuple[str, ...],
    *,
    project: Path,
    env: dict[str, str],
    failure_prefix: str,
) -> None:
    """Materialize this fixture once, then verify that same environment offline.

    A certification fixture is a new execution subject, not the initialized
    Oh-Goon checkout. Native uv may acquire its locked dependencies into the
    selected cache; an inherited native offline setting still applies. The
    following check must neither download nor repair that environment.
    """
    lock = project / "uv.lock"
    before = lock.read_bytes()
    _run_uv(uv, argv, cwd=project, env=env, failure_prefix=failure_prefix)
    if lock.read_bytes() != before:
        raise FleetPlanInvalid(f"native uv rewrote certification lock {lock}")
    _run_uv(
        uv,
        (*argv, "--check", "--offline"),
        cwd=project,
        env={**env, "UV_OFFLINE": "1"},
        failure_prefix=f"offline fixture verification failed after {failure_prefix}",
    )
    if lock.read_bytes() != before:
        raise FleetPlanInvalid(f"native uv rewrote certification lock {lock}")


def _prove_fixture_locks(
    repository: Path, uv: Path, python: Path, scratch: Path, cache: Path
) -> None:
    native_uv = capture_native_uv_authority()
    for index, lock in enumerate(sorted(repository.rglob("uv.lock"))):
        if ".venv" in lock.parts:
            continue
        project = lock.parent
        before = lock.read_bytes()
        preparation = ManagedPreparationPathLayout.from_root(scratch / f"lock-{index}")
        env = native_uv.environment(
            preparation,
            environment=scratch / f"lock-{index}" / "environment",
            cache=cache,
            uv_overrides={
                "UV_NO_MANAGED_PYTHON": "1",
                "UV_PYTHON_DOWNLOADS": "never",
                "UV_PYTHON": str(python),
                "UV_OFFLINE": "1",
            },
        )
        _run_uv(
            uv,
            ("lock", "--check", "--project", str(project), "--offline"),
            cwd=project,
            env=env,
            failure_prefix=f"native uv rejected certification lock {lock.relative_to(repository)}",
        )
        if lock.read_bytes() != before:
            raise FleetPlanInvalid(
                f"native uv rewrote certification lock {lock.relative_to(repository)}"
            )


def _managed_uv_requirements(
    plan: ResolvedFleetPlan,
) -> dict[tuple[str, str | None, str | None], set[str]]:
    requirements: dict[tuple[str, str | None, str | None], set[str]] = {}
    for step in plan.steps:
        if not isinstance(step, ResolvedCommandStep) or step.uv is None:
            continue
        prefix = (
            "uv",
            "run",
            "--frozen",
            "--no-default-groups",
            "--project",
            step.uv.project,
            *(["--group", step.uv.group] if step.uv.group is not None else []),
        )
        if step.argv[: len(prefix)] != prefix or len(step.argv) <= len(prefix):
            raise FleetPlanInvalid(
                f"managed uv step {step.step_id} does not use the canonical resolved uv argv"
            )
        package = step.project_source.content_sha256 if step.project_source is not None else None
        requirements.setdefault((step.uv.project, step.uv.group, package), set()).add(
            step.argv[len(prefix)]
        )
    return requirements


def _direct_uv_project_requirements(
    plan: ResolvedFleetPlan,
) -> dict[str, set[str]]:
    requirements: dict[str, set[str]] = {}
    for step in plan.steps:
        if not isinstance(step, ResolvedCommandStep) or step.uv is not None:
            continue
        argv = step.argv
        if argv[:2] != ("uv", "run"):
            continue
        if "--project" not in argv:
            continue
        if "--frozen" not in argv or "--no-sync" not in argv:
            raise FleetPlanInvalid(
                f"direct uv project step {step.step_id} must use --frozen and --no-sync"
            )
        project_index = argv.index("--project")
        if project_index + 2 >= len(argv):
            raise FleetPlanInvalid(
                f"direct uv project step {step.step_id} has incomplete --project binding"
            )
        project = argv[project_index + 1]
        executable = argv[project_index + 2]
        if executable.startswith("-"):
            raise FleetPlanInvalid(
                f"direct uv project step {step.step_id} has no canonical project executable"
            )
        requirements.setdefault(project, set()).add(executable)
    return requirements


def _prove_managed_uv_projects(
    plan: ResolvedFleetPlan,
    *,
    repository: Path,
    uv: Path,
    python: Path,
    scratch: Path,
    cache: Path,
) -> None:
    native_uv = capture_native_uv_authority()
    package_sources = materialize_plan_projects(
        plan, RunPathLayout.from_root(scratch / "captured-packages")
    )
    for index, ((project_name, group, package), executables) in enumerate(
        sorted(
            _managed_uv_requirements(plan).items(),
            key=lambda item: tuple(value or "" for value in item[0]),
        )
    ):
        project = (
            package_sources[package]
            if package is not None
            else repository.joinpath(*PurePosixPath(project_name).parts)
        )
        environment = scratch / "managed" / f"{index:03d}"
        preparation = ManagedPreparationPathLayout.from_root(scratch / f"managed-{index}")
        env = native_uv.environment(
            preparation,
            environment=environment,
            cache=cache,
            uv_overrides={
                "UV_NO_MANAGED_PYTHON": "1",
                "UV_PYTHON_DOWNLOADS": "never",
                "UV_PYTHON": str(python),
            },
        )
        _materialize_fixture_environment(
            uv,
            (
                "sync",
                "--project",
                str(project),
                "--locked",
                "--no-default-groups",
                *(["--group", group] if group is not None else []),
                *(["--no-editable"] if package is not None else []),
            ),
            project=project,
            env=env,
            failure_prefix=f"native uv rejected project {project_name!r} group {group!r}",
        )
        for executable in sorted(executables):
            if _materialized_executable(environment, executable) is None:
                raise FleetPlanInvalid(
                    f"required project executable `{executable}` is not resolvable"
                )


def _prove_direct_uv_projects(
    plan: ResolvedFleetPlan,
    *,
    repository: Path,
    uv: Path,
    python: Path,
    scratch: Path,
    cache: Path,
) -> None:
    for index, (project_name, executables) in enumerate(
        sorted(_direct_uv_project_requirements(plan).items())
    ):
        project = repository.joinpath(*PurePosixPath(project_name).parts)
        environment = scratch / "direct-uv" / f"{index:03d}"
        env = _native_uv_environment(
            environment,
            cache,
            python,
        )
        _materialize_fixture_environment(
            uv,
            (
                "sync",
                "--project",
                str(project),
                "--locked",
                "--no-default-groups",
            ),
            project=project,
            env=env,
            failure_prefix=f"native uv rejected direct project {project_name!r}",
        )
        for executable in sorted(executables):
            if _materialized_executable(environment, executable) is None:
                raise FleetPlanInvalid(
                    f"required project executable `{executable}` is not resolvable"
                )


def _materialize_direct_package_executables(
    plan: ResolvedFleetPlan,
    *,
    repository: Path,
    uv: Path,
    python: Path,
    scratch: Path,
    cache: Path,
) -> None:
    materialized = {
        relative
        for step in plan.steps
        for relative in logical_step_execution_authority(repository, step).materialized_executables
    }
    projects: set[Path] = set()
    for relative in materialized:
        parts = PurePosixPath(relative).parts
        if ".venv" not in parts:
            raise FleetPlanInvalid(
                f"template certification has no fixture materializer for {relative!r}"
            )
        marker = parts.index(".venv")
        projects.add(repository.joinpath(*parts[:marker]))

    for project in sorted(projects, key=str):
        env = _native_uv_environment(
            project / ".venv",
            cache,
            python,
        )
        _materialize_fixture_environment(
            uv,
            ("sync", "--project", str(project), "--locked", "--no-default-groups"),
            project=project,
            env=env,
            failure_prefix=(
                f"native uv could not materialize direct project executable for {project}"
            ),
        )

    for relative in materialized:
        path = repository.joinpath(*PurePosixPath(relative).parts)
        if not path.is_file() or not os.access(path, os.X_OK):
            raise FleetPlanInvalid(f"required repository executable `{relative}` is not resolvable")


def _prove_repository_paths(plan: ResolvedFleetPlan, repository: Path) -> None:
    for step in plan.steps:
        if not isinstance(step, ResolvedOciBuildStep):
            continue
        containerfile = repository.joinpath(*PurePosixPath(step.containerfile).parts)
        context = repository.joinpath(*PurePosixPath(step.context).parts)
        if not containerfile.is_file():
            raise FleetPlanInvalid(
                f"required OCI Containerfile `{step.containerfile}` is not resolvable"
            )
        if not context.is_dir():
            raise FleetPlanInvalid(f"required OCI build context `{step.context}` is not resolvable")


def _prove_fixture_contract(
    plan: ResolvedFleetPlan,
    *,
    repository: Path,
    path_authority: StrictHostPathAuthority,
    scratch: Path,
) -> None:
    uv = (
        path_authority.tool("uv")
        if "uv" in required_runtime_tool_names(plan)
        else Path(shutil.which("uv") or "")
    )
    if not uv.is_file():
        raise FleetPlanInvalid("template certification requires native uv")
    python = find_uv_system_python(uv)
    if python is None:
        raise FleetPlanInvalid("template certification could not select native uv system Python")

    # Native uv owns artifact caching. Resolve the selected cache once; fixture
    # setup may acquire locked dependencies there before its offline proof.
    # Each fixture environment and its workload scratch remain private.
    selected_cache = subprocess.run(
        (str(uv), "cache", "dir"), check=True, capture_output=True, text=True
    )
    cache = Path(selected_cache.stdout.strip())
    _prove_fixture_locks(repository, uv, python, scratch, cache)
    _materialize_direct_package_executables(
        plan, repository=repository, uv=uv, python=python, scratch=scratch, cache=cache
    )
    _prove_direct_uv_projects(
        plan, repository=repository, uv=uv, python=python, scratch=scratch, cache=cache
    )
    _prove_managed_uv_projects(
        plan, repository=repository, uv=uv, python=python, scratch=scratch, cache=cache
    )
    _prove_repository_paths(plan, repository)


def test_catalog_certification_scenarios_cover_exact_shipped_plan_catalog() -> None:
    plans = PlanTemplateStore(TEMPLATES / "plans")
    discovered = {item.template_key for item in plans.catalog().templates}
    agent_plans = agent_plan_template_keys()
    assert not set(SCENARIOS).intersection(agent_plans)
    assert set(SCENARIOS).union(agent_plans) == discovered
    for template_key, scenario in SCENARIOS.items():
        assert isinstance(scenario, dict), template_key
        fixture_key = scenario["fixture"]
        assert isinstance(fixture_key, str), template_key
        assert FIXTURES.joinpath(*PurePosixPath(fixture_key).parts).is_dir(), template_key
        assert isinstance(scenario["inputs"], dict), template_key


@pytest.mark.parametrize("template_key", sorted(SCENARIOS), ids=sorted(SCENARIOS))
def test_shipped_catalog_plan_reaches_run_admitted_with_canonical_fixture_without_step_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    template_key: str,
) -> None:
    scenario = SCENARIOS[template_key]
    fixture_key = scenario["fixture"]
    inputs = scenario["inputs"]
    assert isinstance(fixture_key, str)
    assert isinstance(inputs, dict)

    repository = tmp_path / "repositories" / "fixture"
    _copy_fixture(fixture_key, repository)
    target = _reviewed_shipped_target(template_key, inputs=inputs)
    for step in target.plan.steps:
        for name in step_host_environment_names(step):
            if name.endswith("CA_CERTIFICATE"):
                value = KUBERNETES_CERTIFICATION_CA
            elif name.endswith("TOKEN"):
                value = KUBERNETES_CERTIFICATION_TOKEN
            else:
                value = "template-certification-value"
            monkeypatch.setenv(name, value)

    path_authority = _fixture_path_authority(target.plan, tmp_path / "executor")
    _prove_fixture_contract(
        target.plan,
        repository=repository,
        path_authority=path_authority,
        scratch=tmp_path / "fixture-proof",
    )
    source_before = _repository_bytes(repository)
    _install_source_identity_observer(monkeypatch, repository)

    database, state, attempts, planner, lifecycle, backend = _admission_stack(
        tmp_path,
        repository,
        path_authority=path_authority,
    )
    repository_identity = RepositoryIdentity.model_validate({"path": repository})
    repository_scope = fleet_repository_scope({repository_identity.repository_id})
    intent = resolve_capability_intent(OperationAction.EXECUTE_PLAN, target)
    admission = planner.plan(intent, repository_scope=repository_scope)
    run_id = "template-catalog-" + template_key.replace("/", "-")
    _ticket, run = lifecycle.admit(
        run_id,
        intent,
        repository_scope,
        [entry.repository for entry in admission.targets],
        expected_durable_revision=admission.durable_revision,
        repository_admissions=admission.repository_admissions,
        source=FleetRunSource.MANUAL,
    )

    events = state.journal.run_events(run_id).events
    assert run.status is FleetRunStatus.QUEUED
    assert [event.event_type for event in events] == [FleetRunEventType.RUN_ADMITTED]
    assert backend.execute_calls == 0
    assert attempts.all() == []
    assert _repository_bytes(repository) == source_before
    assert database.durable_revision() > admission.durable_revision


def test_repository_exam_project_entrypoint_is_proven_without_repository_venv_and_missing_command_fails_before_admission(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repositories" / "fixture"
    _copy_fixture("oh-goon/self-host", repository)
    assert not (repository / "backend/.venv").exists()
    target = _reviewed_shipped_target("oh-goon/repository-understanding-certification")
    path_authority = _fixture_path_authority(target.plan, tmp_path / "executor")

    _prove_fixture_contract(
        target.plan,
        repository=repository,
        path_authority=path_authority,
        scratch=tmp_path / "positive-proof",
    )
    assert not (repository / "backend/.venv").exists()

    pyproject = repository / "backend/pyproject.toml"
    source = pyproject.read_text(encoding="utf-8")
    entrypoint = 'oh-goon-repository-understanding-exam = "oh_goon_self_host_fixture:main"'
    assert entrypoint in source
    pyproject.write_text(source.replace(entrypoint + "\n", ""), encoding="utf-8")

    database, state, attempts, _planner, _lifecycle, backend = _admission_stack(
        tmp_path, repository, path_authority=path_authority
    )
    revision_before = database.durable_revision()
    source_before = _repository_bytes(repository)

    with pytest.raises(
        FleetPlanInvalid,
        match=r"required project executable `oh-goon-repository-understanding-exam` is not resolvable",
    ):
        _prove_fixture_contract(
            target.plan,
            repository=repository,
            path_authority=path_authority,
            scratch=tmp_path / "negative-proof",
        )

    assert state.journal.read_view().run_detail("template-entrypoint-negative") is None
    assert attempts.all() == []
    assert backend.availability_checks == 0
    assert backend.execute_calls == 0
    assert database.durable_revision() == revision_before
    assert _repository_bytes(repository) == source_before
    assert not (repository / "backend/.venv").exists()
