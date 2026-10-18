from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from oh_goon.core.fleet_plan_tool_requirements import step_runtime_tool_names
from oh_goon.core.models.fleet_plan import ResolvedCommandStep
from oh_goon.core.models.fleet_plan_step_contract import PlanStepCapabilities
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.path_authority import StrictHostPathAuthority
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import IsolatedProcessRequest
from tests.factories.bubblewrap_command import prepared_bubblewrap_command as _prepared
from tests.factories.fleet_plan_executor import (
    TEST_ISOLATION_BACKEND,
    prepare_physical_test_run,
    strict_test_plan,
)


def test_tooling_step_projects_exact_declared_children_into_strict_toolchain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="tooling",
        argv=("make", "test-tooling-complete"),
        capabilities=PlanStepCapabilities(tools=("bash", "dd", "git", "sh")),
    )
    expected = step_runtime_tool_names(step)
    toolchain = tmp_path / "admitted-toolchain"
    toolchain.mkdir()
    for name in expected:
        executable = shutil.which(name)
        assert executable is not None, (
            f"strict tooling regression requires host fixture tool {name}"
        )
        (toolchain / name).symlink_to(Path(executable).resolve())

    ambient = tmp_path / "ambient"
    ambient.mkdir()
    ambient_tool = ambient / "ambient-only"
    ambient_tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    ambient_tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{ambient}:{toolchain}")

    ldd = Path("/usr/bin/ldd").resolve()
    authority = StrictHostPathAuthority.create(
        trusted_roots=(toolchain, ldd.parent),
        tool_search_paths=(toolchain,),
        runtime_inspector=ldd,
    )
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "owned.txt").write_text("tooling-source\n", encoding="utf-8")
    (repository / "Makefile").write_text("test-tooling-complete:\n\t@true\n", encoding="utf-8")

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(step),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=authority,
    ) as run:
        attempt = run.create_attempt(step, 1)
        assert tuple(binding.name for binding in attempt.host_tools) == expected
        assert {"bash", "dd", "git", "sh", "make"} <= set(expected)
        assert "ambient-only" not in expected
        prepared = _prepared(
            BubblewrapProcessIsolation(executable="/usr/bin/true"),
            IsolatedProcessRequest(
                attempt=attempt,
                argv=step.argv,
                cwd=attempt.paths.workspace,
                env={},
            ),
        )

    separator = prepared.argv.index("--")
    assert prepared.env["PATH"] == "/toolchain/bin"
    assert str(ambient) not in prepared.env["PATH"]
    for name in expected:
        assert f"/toolchain/bin/{name}" in prepared.argv[:separator]
    assert "/toolchain/bin/ambient-only" not in prepared.argv[:separator]


def test_prepare_physical_test_run_does_not_recompute_explicit_test_authorities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.factories import fleet_plan_executor as factory

    repository = RepositoryIdentity(path=tmp_path / "repository")
    repository.path.mkdir()
    plan = strict_test_plan(ResolvedCommandStep(timeout_seconds=60, step_id="noop", argv=("true",)))
    explicit_source = object()
    explicit_path = object()
    captured: dict[str, object] = {}

    def forbidden_source_default(_repository: RepositoryIdentity) -> object:
        raise AssertionError("explicit source authority must not evaluate the default")

    def forbidden_path_default(_plan: object) -> object:
        raise AssertionError("explicit path authority must not evaluate the default")

    def capture_prepared_run(
        _repository: RepositoryIdentity, _plan: object, **kwargs: object
    ) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(factory, "source_authority_for_test", forbidden_source_default)
    monkeypatch.setattr(factory, "test_path_authority_for_plan", forbidden_path_default)
    monkeypatch.setattr(factory, "PreparedPlanRun", capture_prepared_run)

    factory.prepare_physical_test_run(
        repository,
        plan,
        source_authority=explicit_source,
        path_authority=explicit_path,
        process_isolation=object(),
        isolation_backend=TEST_ISOLATION_BACKEND,
    )

    assert captured["source_authority"] is explicit_source
    assert captured["path_authority"] is explicit_path
