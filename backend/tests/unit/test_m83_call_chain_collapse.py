from __future__ import annotations

import stat
from pathlib import Path

from oh_goon.core.models.fleet_plan_diagnostics import PlanResolutionDisposition
from oh_goon.integrations import package_script_execution_authority as package_scripts
from oh_goon.repository_tooling.architecture import (
    _structural_project_dependency_contract as dependency_contract,
)
from oh_goon.runtime._executor_local_capacity import LocalExecutorCapacity
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.schedule_scheduler import ScheduleScheduler
from oh_goon.services import certification_bootstrap
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def test_fleet_plan_diagnostics_survive_direct_owner_calls(tmp_path: Path) -> None:
    goon = tmp_path / "goons" / "general" / "leaf"
    goon.mkdir(parents=True)
    (goon / "README.md").write_text("# general/leaf\n", encoding="utf-8")
    (goon / "goon.yml").write_text(
        """version: 1
inputs:
  token:
    type: string
steps:
- run: echo ${{ inputs.token }}
""",
        encoding="utf-8",
    )
    plan = tmp_path / "plan.yml"
    plan.write_text(
        """version: 1
goons:
- import: general/leaf
  with:
    token:
      from:
        env: RELEASE_TOKEN
""",
        encoding="utf-8",
    )

    report = FleetPlanResolver(goon_templates=GoonTemplateStore(tmp_path / "goons")).explain(
        plan, environment={"RELEASE_TOKEN": "super-secret"}
    )

    value = next(item for item in report.values if item.name == "token")
    assert value.origin == "env:RELEASE_TOKEN"
    assert value.resolved == "<env:RELEASE_TOKEN:redacted>"
    assert value.contains_host_value is True
    expanded = next(
        item for item in report.entries if item.disposition is PlanResolutionDisposition.EXPANDED
    )
    assert expanded.step_id == "general-leaf"
    assert expanded.compiled_step_ids == ("general-leaf.echo",)
    assert "super-secret" not in report.model_dump_json()


def test_scheduler_loop_uses_public_tick_owner() -> None:
    scheduler = ScheduleScheduler(lambda: None, interval_seconds=60)
    waits = iter((False, True))
    scheduler._stop = type(  # type: ignore[assignment]
        "Stop", (), {"wait": lambda _self, _timeout: next(waits)}
    )()
    calls: list[str] = []
    scheduler.tick = lambda: calls.append("tick")  # type: ignore[method-assign]

    scheduler._loop()

    assert calls == ["tick"]
    assert not hasattr(ScheduleScheduler, "_run_tick")


def test_npm_script_parser_has_one_public_implementation() -> None:
    assert package_scripts.npm_run_script(("npm", "test"), 0) == "test"
    assert package_scripts.npm_run_script(("npm", "run", "--silent", "build"), 0) == "build"
    assert package_scripts.npm_run_script(("npm", "run-script", "--", "build"), 0) == "build"
    assert package_scripts.npm_run_script(("npm", "exec", "build"), 0) is None
    assert not hasattr(package_scripts, "_npm_run_script")


def test_local_executor_inventory_has_one_materialization_owner(
    tmp_path: Path, monkeypatch
) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    bwrap = tools / "bwrap"
    ldd = tools / "ldd"
    monkeypatch.setattr(
        certification_bootstrap,
        "required_certification_tool",
        lambda name: {"bwrap": bwrap, "ldd": ldd}[name],
    )
    cgroup = tmp_path / "cgroup"
    monkeypatch.setattr(
        certification_bootstrap,
        "resolve_certification_cgroup_root",
        lambda _environment: cgroup,
    )
    monkeypatch.setattr(
        certification_bootstrap,
        "observe_local_executor_capacity",
        lambda _root: LocalExecutorCapacity(
            cpu_millis=2000,
            memory_bytes=6 * 1024 * 1024 * 1024,
            max_processes=64,
        ),
    )
    monkeypatch.setattr(
        certification_bootstrap,
        "certification_tool_search_paths",
        lambda _environment: (tools,),
    )

    inventory = certification_bootstrap.materialize_local_executor_inventory(
        state=tmp_path, environment={}
    )

    text = inventory.read_text(encoding="utf-8")
    assert f"version = {certification_bootstrap.EXECUTOR_CONFIG_VERSION}" in text
    assert 'id = "certification-local"' in text
    assert f'bwrap_executable = "{bwrap}"' in text
    assert f'cgroup_root = "{cgroup}"' in text
    assert stat.S_IMODE(inventory.stat().st_mode) == 0o600
    assert not hasattr(certification_bootstrap, "_materialize_local_executor_inventory")


def test_browser_dependency_guard_has_one_implementation(tmp_path: Path) -> None:
    browser = (
        tmp_path
        / "backend"
        / "src"
        / "oh_goon"
        / "repository_tooling"
        / "release"
        / "browser_acceptance_container.py"
    )
    browser.parent.mkdir(parents=True)
    browser.write_text(
        "import json\n"
        "from pathlib import Path\n"
        "authority = Path('frontend/package-lock.json')\n"
        "payload = json.loads(authority.read_text())\n",
        encoding="utf-8",
    )

    issues = dependency_contract._check_browser_dependency_identity(tmp_path)

    assert len(issues) == 1
    assert "must not parse package-lock semantics in parallel with npm" in issues[0].message
    assert not hasattr(dependency_contract, "_check_browser_identity")
