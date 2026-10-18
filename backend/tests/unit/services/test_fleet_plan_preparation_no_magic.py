from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest

from oh_goon.engine.command import CommandResult
from oh_goon.engine.fleet_extensions import FleetExtensionStore
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import FleetPlanService
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.schedules import ScheduleService
from tests.factories.path_authority import TEST_PATH_AUTHORITY


def test_same_precommit_runtime_preparation_mutates_environment_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_barrier = threading.Barrier(2)
    observation_lock = threading.Lock()
    mutating_syncs = 0

    def fake_run_command(*argv: str, **kwargs) -> CommandResult:
        nonlocal mutating_syncs
        project = Path(argv[argv.index("--project") + 1])
        if argv[1] == "lock":
            (project / "uv.lock").write_text("version = 1\n", encoding="utf-8")
            lock_barrier.wait(timeout=5)
        elif argv[1] == "sync":
            environment = Path(cast(dict[str, str], kwargs["env"])["UV_PROJECT_ENVIRONMENT"])
            executable = (
                environment
                / ("Scripts" if os.name == "nt" else "bin")
                / ("pre-commit.exe" if os.name == "nt" else "pre-commit")
            )
            if "--check" in argv:
                return CommandResult(
                    argv=tuple(argv),
                    returncode=0 if executable.is_file() else 1,
                    stdout="",
                    stderr="",
                    duration_ms=1,
                )
            with observation_lock:
                mutating_syncs += 1
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text("", encoding="utf-8")
        return CommandResult(argv=tuple(argv), returncode=0, stdout="", stderr="", duration_ms=1)

    monkeypatch.setattr("oh_goon.engine.fleet_tools.run_command", fake_run_command)
    store = FleetToolStore(tmp_path / "plan-tools", path_authority=TEST_PATH_AUTHORITY)
    with ThreadPoolExecutor(max_workers=2) as pool:
        runtimes = tuple(
            pool.map(lambda _: store.prepare_pre_commit("pre-commit==4.6.2"), range(2))
        )

    assert runtimes[0] == runtimes[1]
    assert mutating_syncs == 1


def test_plan_prepare_does_not_acquire_unreferenced_extension_catalog_entries(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(
        """version: 1
execution:
  purpose: verification
steps:
  - id: tests
    run: "true"
""",
        encoding="utf-8",
    )
    catalog = tmp_path / "extensions.toml"
    catalog.write_text(
        """version = 2

[[extensions]]
name = "unused"
source = "https://example.invalid/unused.git"
revision = "0123456789abcdef0123456789abcdef01234567"
commands = ["unused"]
""",
        encoding="utf-8",
    )

    class NoAcquisitionExtensions:
        def sync(self, *args, **kwargs):
            raise AssertionError("unreferenced extension must not be acquired")

    class NoToolPreparation:
        def prepare_pre_commit(self, requirement: str):
            raise AssertionError(f"unreferenced built-in tool must not be prepared: {requirement}")

    extension_store = cast(FleetExtensionStore, NoAcquisitionExtensions())
    tools = cast(FleetToolStore, NoToolPreparation())
    service = FleetPlanService(
        FleetPlanResolver(extension_store, tools),
        cast(FleetRunControlPlane, object()),
        cast(ScheduleService, object()),
        extension_store,
        tools,
    )

    plan, imported = service.prepare(plan_path, extensions_path=catalog)

    assert plan.name == "goon"
    assert imported == ()


def test_plan_prepare_reads_each_shipped_goon_once_and_reuses_the_graph(
    tmp_path: Path,
) -> None:
    from oh_goon.runtime.goon_templates import GoonTemplateStore

    packages = tmp_path / "goons"
    package = packages / "general" / "shared"
    package.mkdir(parents=True)
    (package / "goon.yml").write_text(
        """version: 1
steps:
  - id: check
    run: "true"
""",
        encoding="utf-8",
    )
    (package / "README.md").write_text("# shared\n", encoding="utf-8")
    root = tmp_path / "root.yml"
    root.write_text(
        """version: 1
goons:
  - import: general/shared
  - import: general/shared
""",
        encoding="utf-8",
    )

    class CountingGoons:
        def __init__(self) -> None:
            self.store = GoonTemplateStore(packages)
            self.calls = 0

        def source(self, template_key: str):
            self.calls += 1
            return self.store.source(template_key)

    class NoExtensions:
        def sync(self, *args, **kwargs):
            raise AssertionError("no extensions are declared")

    class NoTools:
        def prepare_pre_commit(self, requirement: str):
            raise AssertionError("pre-commit is not declared")

    from oh_goon.integrations import fleet_plan_source_snapshot as source_snapshot_module

    parse_calls = 0
    original_parse = source_snapshot_module.parse_fleet_yaml_document

    def counted_parse(content: str, *, source: str):
        nonlocal parse_calls
        parse_calls += 1
        return original_parse(content, source=source)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(source_snapshot_module, "parse_fleet_yaml_document", counted_parse)
    goons = CountingGoons()
    extensions = cast(FleetExtensionStore, NoExtensions())
    tools = cast(FleetToolStore, NoTools())
    resolver = FleetPlanResolver(extensions, tools, goon_templates=goons)
    service = FleetPlanService(
        resolver,
        cast(FleetRunControlPlane, object()),
        cast(ScheduleService, object()),
        extensions,
        tools,
    )

    plan, imported = service.prepare(root)

    assert imported == ()
    assert [step.step_id for step in plan.steps] == [
        "general-shared.check",
        "general-shared-2.check",
    ]
    assert goons.calls == 1
    assert parse_calls == 2  # root Plan + one unique imported Goon
    monkeypatch.undo()


def test_managed_tool_project_syncs_before_publish_and_surfaces_post_publish_durability_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.engine import fleet_tools as module
    from oh_goon.integrations.exact_file_write import ExactPublicationDurabilityError

    events: list[str] = []

    def fake_run_command(*argv: str, **_kwargs: object) -> CommandResult:
        project = Path(argv[argv.index("--project") + 1])
        if argv[1] == "lock":
            (project / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        return CommandResult(argv=tuple(argv), returncode=0, stdout="", stderr="", duration_ms=1)

    def publish(staging: Path, target: Path) -> None:
        events.append("publish")
        staging.rename(target)
        raise ExactPublicationDurabilityError("managed tool parent sync failed")

    monkeypatch.setattr(module, "run_command", fake_run_command)
    monkeypatch.setattr(module, "sync_prepared_path", lambda _path: events.append("sync"))
    monkeypatch.setattr(module, "publish_prepared_directory", publish)
    store = FleetToolStore(tmp_path / "plan-tools", path_authority=TEST_PATH_AUTHORITY)

    with pytest.raises(ExactPublicationDurabilityError, match="managed tool parent sync failed"):
        store.prepare_pre_commit("pre-commit==4.6.2")

    assert events == ["sync", "publish"]
    assert tuple(
        path for path in store.root.iterdir() if path.is_dir() and not path.name.startswith(".")
    )


def test_managed_extension_syncs_before_publish_and_surfaces_post_publish_durability_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.core.models.fleet_plan import ExtensionDeclaration, ManagedExtension
    from oh_goon.engine import fleet_extensions as module
    from oh_goon.integrations.exact_file_write import ExactPublicationDurabilityError

    source = tmp_path / "extension-source"
    source.mkdir()
    declaration = ExtensionDeclaration(
        name="quality",
        source="extension-source",
        revision="a" * 40,
        commands=("quality",),
    )
    events: list[str] = []

    class FakeGit:
        def run(self, *argv: str, **_kwargs: object) -> CommandResult:
            if "clone" in argv:
                destination = Path(argv[-1])
                destination.mkdir()
                (destination / "payload").write_text("prepared", encoding="utf-8")
            return CommandResult(
                argv=tuple(argv), returncode=0, stdout="", stderr="", duration_ms=1
            )

    def validate(
        _self: FleetExtensionStore,
        project_path: Path,
        _declaration: ExtensionDeclaration,
        canonical_source: str,
        extension_id: str,
    ) -> ManagedExtension:
        return ManagedExtension(
            extension_id=extension_id,
            source=canonical_source,
            revision=declaration.revision,
            project_name="quality",
            project_path=project_path,
            project_fingerprint="prepared",
            pyproject_sha256="b" * 64,
            uv_lock_sha256="c" * 64,
            scripts=("quality",),
        )

    def publish(staging: Path, target: Path) -> None:
        events.append("publish")
        staging.rename(target)
        raise ExactPublicationDurabilityError("managed extension parent sync failed")

    monkeypatch.setattr(module, "_git_service", lambda _authority: FakeGit())
    monkeypatch.setattr(FleetExtensionStore, "_validate_checkout", validate)
    monkeypatch.setattr(module, "_ensure_environment", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "sync_prepared_path", lambda _path: events.append("sync"))
    monkeypatch.setattr(module, "publish_prepared_directory", publish)
    store = FleetExtensionStore(tmp_path / "extensions", path_authority=TEST_PATH_AUTHORITY)

    with pytest.raises(
        ExactPublicationDurabilityError, match="managed extension parent sync failed"
    ):
        store.sync(declaration, catalog_root=tmp_path)

    assert events == ["sync", "publish"]
    assert tuple(
        path for path in store.root.iterdir() if path.is_dir() and not path.name.startswith(".")
    )
