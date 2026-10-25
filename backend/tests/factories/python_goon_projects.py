"""Complete Python package fixtures using original runtime and native uv owners."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import TypedDict

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.product_identity import repository_id, repository_subject_instance_id
from oh_goon.runtime import goon_templates
from oh_goon.runtime.league_folders import LeagueFolderStore

SHOWCASE = Path(__file__).resolve().parents[3] / "examples/airflow-taskflow-showcase"


class SharedPackageScheduleOptions(TypedDict):
    name: str
    repository_ids: tuple[str, ...]
    expected_repository_instance_ids: dict[str, str]
    plan_key: str
    expected_plan_revision: str
    expected_plan_id: str
    expected_execution_digest: str
    interval_minutes: int


@pytest.fixture
def execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[ApplicationRuntime, Path, Path]]:
    repository = tmp_path / "repositories/target"
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "runtime.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    LeagueFolderStore().replace((repository,))
    workflow = tmp_path / "workflow"
    package = workflow / "goons/general/orders"
    shutil.copytree(
        SHOWCASE,
        package,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".showcase"),
    )
    (package / "README.md").write_text("# general/orders\n\nRun a complete Python package.\n")
    plan = workflow / "plan.yml"
    plan.write_text("goons:\n- import: general/orders\n  from: goons\n")
    runtime = ApplicationRuntime.build(standard_local=True)
    runtime.locations.add(repository.parent)
    try:
        yield runtime, repository, plan
    finally:
        runtime.shutdown(wait_for_runs=True)


@pytest.fixture
def shared_execution(
    execution: tuple[ApplicationRuntime, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[ApplicationRuntime, Path, Path, SharedPackageScheduleOptions]]:
    previous, repository, plan = execution
    previous.shutdown(wait_for_runs=True)
    # Supply a complete installed-catalog fixture to the original source provider;
    # compilation, capture, persistence and native execution retain their real owners.
    monkeypatch.setattr(
        goon_templates, "default_goon_templates_root", lambda: plan.parent / "goons"
    )
    runtime = ApplicationRuntime.build(standard_local=True)
    document = runtime.shared_plan.save("orders", "goons:\n- general/orders\n")
    preview = runtime.shared_plan.lock_preview(document.plan_key)
    options: SharedPackageScheduleOptions = {
        "name": "Orders",
        "repository_ids": (repository_id(repository),),
        "expected_repository_instance_ids": {
            repository_id(repository): repository_subject_instance_id(repository),
        },
        "plan_key": document.plan_key,
        "expected_plan_revision": document.sha256,
        "expected_plan_id": preview.plan_id,
        "expected_execution_digest": preview.execution_digest,
        "interval_minutes": 60,
    }
    try:
        yield runtime, repository, plan, options
    finally:
        runtime.shutdown(wait_for_runs=True)
