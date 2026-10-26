from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, call

import pytest

from oh_goon.core.errors import FleetPlanInvalid, FleetPlanSourceNotFound
from oh_goon.core.fleet_plan_tool_requirements import (
    required_runtime_tool_names,
    shell_runtime_support_tool_names,
)
from oh_goon.integrations.fleet_plan_compilation import parse_plan_template
from oh_goon.integrations.workload_execution_requirements import (
    load_workload_execution_requirements,
    make_target_owner,
    required_tools_for_owner,
)
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.shipped_templates import GOON_TEMPLATES as SHIPPED_GOONS
from tests.factories.shipped_templates import PLAN_TEMPLATES as SHIPPED_PLANS

ROOT = Path(__file__).resolve().parents[4]


def _load_shipped_template_fixtures() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_oh_goon_test_shipped_templates", ROOT / "backend/tests/factories/shipped_templates.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generic_shipped_fixture_import_does_not_discover_agent_plans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_keys = Mock(side_effect=AssertionError("Import must not scan the shipped Plan catalog"))
    analyze = Mock(side_effect=AssertionError("Import must not analyze shipped Plan sources"))
    monkeypatch.setattr(PlanTemplateStore, "keys", plan_keys)
    monkeypatch.setattr(FleetPlanResolver, "analyze_source", analyze)

    _load_shipped_template_fixtures()

    plan_keys.assert_not_called()
    analyze.assert_not_called()


def test_agent_plan_fixture_membership_uses_compiler_results_once_on_demand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixtures = _load_shipped_template_fixtures()
    agent_sources = {
        "general/agent": ("review",),
        "agents/source-only": (),
        "general/tool": (),
        "general/another-agent": ("review", "coding"),
    }
    plans = SimpleNamespace(
        keys=lambda: tuple(agent_sources),
        source=lambda key: SimpleNamespace(path=Path(key)),
    )
    analyze = Mock(
        side_effect=lambda path: SimpleNamespace(
            agent_step_sources=lambda: agent_sources[path.as_posix()]
        )
    )
    monkeypatch.setattr(fixtures, "PLAN_TEMPLATES", plans)
    monkeypatch.setattr(FleetPlanResolver, "analyze_source", analyze)

    members = fixtures.agent_plan_template_keys()

    assert members == ("general/agent", "general/another-agent")
    assert analyze.call_args_list == [call(Path(key)) for key in agent_sources]
    assert fixtures.agent_plan_template_keys() is members
    assert analyze.call_count == len(agent_sources)


def _workload_tools(owner: str) -> tuple[str, ...]:
    tools = required_tools_for_owner(load_workload_execution_requirements(ROOT), owner)
    support = set(shell_runtime_support_tool_names(tools))
    return tuple(tool for tool in tools if tool not in support)


def _assert_make_tool_authority(plan) -> tuple[str, ...]:
    expected = {"make"}
    for step in plan.steps:
        owner = make_target_owner(step.argv)
        assert owner is not None, (step.step_id, step.argv)
        tools = _workload_tools(owner)
        assert tuple(step.capabilities.tools) == tools, (step.step_id, owner, tools)
        expected.update(tools)
    return tuple(sorted(expected))


def _package(root: Path, key: str, *, redundant_name: str | None = None) -> Path:
    category, package = key.split("/")
    directory = root / category / package
    directory.mkdir(parents=True)
    metadata = f"name: {redundant_name}\n" if redundant_name is not None else ""
    (directory / "plan.yml").write_text(
        f"""version: 1\n{metadata}goons:
- python/quality
""",
        encoding="utf-8",
    )
    (directory / "README.md").write_text(f"# {key}\n", encoding="utf-8")
    return directory


def test_shipped_plan_catalog_is_category_name_and_contains_only_rosters() -> None:
    store = SHIPPED_PLANS
    assert store.keys() == tuple(sorted(store.keys()))
    assert "python/secure" in store.keys()
    assert all(key.count("/") == 1 for key in store.keys())
    assert store.runnable_keys() == store.keys()
    for key in store.keys():
        source = store.source(key)
        assert source.compiled.execution is not None
        assert source.compiled.steps
        assert all(getattr(step, "kind", None) == "goon" for step in source.compiled.steps)


def test_plan_parser_forbids_executable_and_implementation_fields() -> None:
    base = """version: 1\n"""
    for fragment in (
        "steps:\n  - run: true\n",
        "run: echo hi\n",
        "command: echo hi\n",
        "script: ./ci.sh\n",
        "imports: [python/quality]\n",
        "requirements:\n- kind: file_exists\n  path: marker\n",
        "env: {}\n",
        "variables: {}\n",
        "outputs: {}\n",
    ):
        with pytest.raises(FleetPlanInvalid):
            parse_plan_template(base + fragment)


def test_plan_parser_requires_non_empty_ordered_goon_roster() -> None:
    with pytest.raises(FleetPlanInvalid, match="non-empty goons list"):
        parse_plan_template("""version: 1\n""")


def test_plan_package_is_exact_two_file_source(tmp_path: Path) -> None:
    directory = _package(tmp_path, "general/valid")
    store = PlanTemplateStore(tmp_path)
    assert store.source("general/valid").compiled.name == "valid"
    (directory / "helper.yml").write_text("version: 1\n", encoding="utf-8")
    with pytest.raises(FleetPlanSourceNotFound, match="unexpected helper.yml"):
        store.source("general/valid")


def test_plan_package_ignores_windows_zone_identifier_transport_sidecars(
    tmp_path: Path,
) -> None:
    directory = _package(tmp_path, "general/valid")
    (directory / "README.md:Zone.Identifier").write_text("[ZoneTransfer]\n", encoding="utf-8")
    (directory / "plan.yml:Zone.Identifier").write_text("[ZoneTransfer]\n", encoding="utf-8")

    store = PlanTemplateStore(tmp_path)

    assert store.source("general/valid").compiled.name == "valid"


def test_plan_template_key_is_exact_category_name(tmp_path: Path) -> None:
    store = PlanTemplateStore(tmp_path)
    for invalid in (
        "standard",
        "Python/standard",
        "python_standard/x",
        "python/standard/extra",
        "../escape",
    ):
        with pytest.raises((FleetPlanSourceNotFound, FleetPlanInvalid), match="category/name"):
            store.source(invalid)


def test_shipped_plan_rejects_redundant_yaml_identity(tmp_path: Path) -> None:
    _package(tmp_path, "python/secure", redundant_name="python-secure")
    with pytest.raises(FleetPlanInvalid, match="unsupported fields: name"):
        PlanTemplateStore(tmp_path).source("python/secure")


def test_shipped_plan_resolves_only_goons_and_preserves_hierarchy() -> None:
    source = SHIPPED_PLANS.source("python/secure")
    plan = FleetPlanResolver(goon_templates=SHIPPED_GOONS).resolve(source.path)

    assert plan.name == "secure"
    assert [item.name for item in plan.imports] == [
        "python/quality",
        "python/ruff",
        "python/typecheck",
        "python/test",
        "security/cve-combined",
        "python/uv-audit",
        "security/trivy-audit",
    ]
    combined = next(item for item in plan.imports if item.name == "security/cve-combined")
    nested = [item for item in plan.imports if item.parent_import_id == combined.import_id]
    assert [item.name for item in nested] == ["python/uv-audit", "security/trivy-audit"]


def test_plan_source_snapshot_is_exact_bytes_and_readme_is_human_only(tmp_path: Path) -> None:
    directory = _package(tmp_path, "general/byte-exact")
    path = directory / "plan.yml"
    raw = path.read_bytes().replace(b"\n", b"\r\n")
    path.write_bytes(raw)
    store = PlanTemplateStore(tmp_path)
    before = store.source("general/byte-exact")
    (directory / "README.md").write_text("# New docs\n", encoding="utf-8")
    after = store.source("general/byte-exact")
    assert before.content.encode("utf-8") == raw
    assert before.sha256 == hashlib.sha256(raw).hexdigest()
    assert after.sha256 == before.sha256
    assert after.compiled == before.compiled


def test_oh_goon_app_certification_plan_verifies_initialized_product_without_reacquisition() -> (
    None
):
    source = SHIPPED_PLANS.source("oh-goon/app-certification")
    plan = FleetPlanResolver(goon_templates=SHIPPED_GOONS).resolve(source.path, inputs={})

    assert plan.execution.purpose.value == "verification"
    assert [item.name for item in plan.imports] == [
        "oh-goon/backend-p0",
        "oh-goon/architecture",
        "oh-goon/template-startability",
        "oh-goon/frontend-functional",
        "oh-goon/local-runtime",
    ]
    release_only = {
        "oh-goon/release-locks",
        "oh-goon/release-identity",
        "oh-goon/release-package-store",
    }
    assert release_only.isdisjoint(item.name for item in plan.imports)
    assert [step.argv for step in plan.steps] == [
        ("make", "verify-p0"),
        ("make", "architecture"),
        ("make", "template-startability-certification"),
        ("make", "frontend-functional-certification"),
        ("make", "local-runtime-verification"),
    ]
    assert all(step.workspace.source.kind == "repository" for step in plan.steps)
    assert all(step.capabilities.egress.mode == "none" for step in plan.steps)
    assert all(step.argv != ("make", "init") for step in plan.steps)


def test_oh_goon_prepare_release_plan_packages_exact_repository_authority_only() -> None:
    source = SHIPPED_PLANS.source("oh-goon/prepare-release")
    plan = FleetPlanResolver(goon_templates=SHIPPED_GOONS).resolve(source.path, inputs={})

    assert [item.name for item in plan.imports] == ["oh-goon/release-package-store"]
    assert len(plan.steps) == 1
    package = plan.steps[0]
    assert package.argv == (
        "uv",
        "run",
        "--frozen",
        "--no-default-groups",
        "--project",
        "backend",
        "--group",
        "build",
        "oh-goon-package-source",
    )
    assert package.uv is not None
    assert package.uv.project == "backend"
    assert package.uv.group == "build"
    assert package.workspace.source.kind == "repository"
    assert package.workspace.publish is False
    assert [artifact.path for artifact in package.artifacts] == [
        "release-artifacts/source.zip",
        "release-artifacts/source.zip.sha256",
        "release-artifacts/source-manifest.json",
    ]
    assert package.artifacts[0].retention == "content_store"


def test_strict_test_path_authority_exposes_only_declared_backend_p0_tools() -> None:
    from oh_goon.core.fleet_plan_tool_requirements import SHELL_RUNTIME_SUPPORT_TOOLS
    from oh_goon.repository_tooling.goon_static_admission import canonical_step_required_tools
    from tests.factories.path_authority import TEST_PATH_AUTHORITY

    required = set(canonical_step_required_tools(ROOT, goon="oh-goon/backend-p0", step_id="make"))
    search_root = TEST_PATH_AUTHORITY.tool_search_paths[0]
    exposed = {path.name for path in search_root.iterdir()}
    assert exposed == required | set(SHELL_RUNTIME_SUPPORT_TOOLS)
    assert {"cat", "cp", "rm", "sha256sum"} <= exposed
    assert {"curl", "buildah", "skopeo"}.isdisjoint(exposed)


def test_plan_scoped_test_path_authority_does_not_inherit_backend_p0_tools() -> None:
    from oh_goon.core.models.fleet_plan import (
        PlanStepCapabilities,
        ResolvedCommandStep,
        ResolvedFleetPlan,
    )
    from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
    from tests.factories.path_authority import test_path_authority_for_plan

    plan = ResolvedFleetPlan.create(
        name="node-only-test-plan",
        description="",
        execution=CicdExecutionPolicy(),
        requirements=(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=30,
                step_id="version",
                argv=("npm", "--version"),
                capabilities=PlanStepCapabilities(tools=("node",)),
            ),
        ),
    )
    authority = test_path_authority_for_plan(plan)
    exposed = {path.name for path in authority.tool_search_paths[0].iterdir()}

    assert exposed == set(required_runtime_tool_names(plan)) == {"node", "npm"}
    assert {"git", "uv"}.isdisjoint(exposed)
