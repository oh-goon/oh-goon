from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_plan_preview import preview_plan
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def _package(root: Path, key: str, body: str) -> None:
    category, name = key.split("/")
    directory = root / category / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "goon.yml").write_text(body, encoding="utf-8")
    (directory / "README.md").write_text(f"# {key}\n", encoding="utf-8")


def _plan(tmp_path: Path, goons: str, *, inputs: str = "") -> Path:
    path = tmp_path / "plan.yml"
    path.write_text(
        f"""version: 1\n{inputs}goons:\n{goons}""",
        encoding="utf-8",
    )
    return path


def _atomic(name: str, *, run: str = "true", requirements: str = "") -> str:
    requirement_block = f"requirements:\n{requirements}\n" if requirements else ""
    return f"""version: 1\n{requirement_block}steps:\n  - id: check\n    run: "{run}"\n"""


def test_minimal_repository_goon_inherits_executor_capacity_by_default(tmp_path: Path) -> None:
    root = tmp_path / "goon.yml"
    root.write_text(
        """version: 1\nsteps:\n  - id: check\n    run: 'true'\n""",
        encoding="utf-8",
    )
    plan = FleetPlanResolver().resolve(root)
    assert plan.execution.trust_repository_code is True
    assert plan.execution.resources.cpu_millis is None
    assert plan.execution.resources.memory_mib is None
    assert plan.execution.resources.max_processes is None


def test_composition_order_and_duplicates_are_preserved(tmp_path: Path) -> None:
    store_root = tmp_path / "goons"
    for key in ("general/a", "general/b", "general/c"):
        _package(store_root, key, _atomic(key.split("/")[1]))
    root = _plan(
        tmp_path,
        "  - import: general/a\n  - import: general/b\n  - import: general/a\n  - import: general/c\n",
    )
    plan = FleetPlanResolver(goon_templates=GoonTemplateStore(store_root)).resolve(root)
    assert [item.name for item in plan.imports] == [
        "general/a",
        "general/b",
        "general/a",
        "general/c",
    ]
    assert [step.step_id for step in plan.steps] == [
        "general-a.check",
        "general-b.check",
        "general-a-2.check",
        "general-c.check",
    ]
    first_a, _, second_a, _ = plan.imports
    assert first_a.import_id == "general-a"
    assert second_a.import_id == "general-a-2"
    assert first_a.source_sha256 == second_a.source_sha256
    assert first_a.step_ids == ("general-a.check",)
    assert second_a.step_ids == ("general-a-2.check",)


def test_nested_composite_goon_preserves_parent_provenance(tmp_path: Path) -> None:
    store_root = tmp_path / "goons"
    _package(store_root, "general/leaf", _atomic("leaf"))
    _package(
        store_root,
        "general/bundle",
        """version: 1\ngoons:\n  - import: general/leaf\n""",
    )
    root = _plan(tmp_path, "  - import: general/bundle\n")
    plan = FleetPlanResolver(goon_templates=GoonTemplateStore(store_root)).resolve(root)

    assert [item.name for item in plan.imports] == ["general/bundle", "general/leaf"]
    bundle, leaf = plan.imports
    assert bundle.parent_import_id is None
    assert leaf.parent_import_id == bundle.import_id
    assert leaf.step_ids == bundle.step_ids
    assert leaf.source_sha256 != bundle.source_sha256


def test_composition_cycle_fails_before_execution(tmp_path: Path) -> None:
    store_root = tmp_path / "goons"
    _package(
        store_root,
        "general/a",
        """version: 1\ngoons:\n  - import: general/b\n""",
    )
    _package(
        store_root,
        "general/b",
        """version: 1\ngoons:\n  - import: general/a\n""",
    )
    root = _plan(tmp_path, "  - import: general/a\n")
    with pytest.raises(
        FleetPlanInvalid,
        match=r"Invalid Goon composition cycle: general/a -> general/b -> general/a",
    ):
        FleetPlanResolver(goon_templates=GoonTemplateStore(store_root)).resolve(root)


def test_composite_projected_input_requires_explicit_forwarding(tmp_path: Path) -> None:
    store_root = tmp_path / "goons"
    _package(
        store_root,
        "general/leaf",
        """version: 1
inputs:
  severity:
    type: string
    default: child-default
steps:
  - id: check
    run: "echo ${{ inputs.severity }}"
""",
    )
    _package(
        store_root,
        "general/bundle",
        """version: 1
inputs: [severity]
goons:
  - import: general/leaf
""",
    )
    root = _plan(tmp_path, "  - import: general/bundle\n")
    with pytest.raises(FleetPlanInvalid, match="must be explicitly forwarded"):
        FleetPlanResolver(goon_templates=GoonTemplateStore(store_root)).resolve(root)


def test_explicit_input_forwarding_crosses_goon_boundary(tmp_path: Path) -> None:
    store_root = tmp_path / "goons"
    _package(
        store_root,
        "general/leaf",
        """version: 1
inputs:
  severity:
    type: string
    default: child-default
steps:
  - id: check
    run: "echo ${{ inputs.severity }}"
""",
    )
    _package(
        store_root,
        "general/bundle",
        """version: 1
inputs: [severity]
goons:
  - import: general/leaf
    with:
      severity: ${{ inputs.severity }}
""",
    )
    root = _plan(
        tmp_path,
        "  - import: general/bundle\n    with:\n      severity: supplied-parent\n",
    )
    plan = FleetPlanResolver(goon_templates=GoonTemplateStore(store_root)).resolve(root)
    assert plan.steps[0].argv == ("echo", "supplied-parent")


def test_composite_projection_can_share_one_compatible_child_contract(tmp_path: Path) -> None:
    store_root = tmp_path / "goons"
    for key in ("general/a", "general/b"):
        _package(
            store_root,
            key,
            """version: 1
inputs:
  severity:
    type: choice
    choices: [low, high]
    default: low
steps:
  - run: echo ${{ inputs.severity }}
""",
        )
    _package(
        store_root,
        "general/bundle",
        """version: 1
inputs: [severity]
goons:
- import: general/a
  with:
    severity: ${{ inputs.severity }}
- import: general/b
  with:
    severity: ${{ inputs.severity }}
""",
    )
    root = _plan(
        tmp_path,
        "  - import: general/bundle\n    with:\n      severity: high\n",
    )
    plan = FleetPlanResolver(goon_templates=GoonTemplateStore(store_root)).resolve(root)
    assert [step.argv for step in plan.steps] == [("echo", "high"), ("echo", "high")]


def test_composite_projection_rejects_incompatible_child_contracts(tmp_path: Path) -> None:
    store_root = tmp_path / "goons"
    _package(
        store_root,
        "general/a",
        """version: 1
inputs:
  value:
    type: string
steps:
- run: echo ${{ inputs.value }}
""",
    )
    _package(
        store_root,
        "general/b",
        """version: 1
inputs:
  value:
    type: hostname
steps:
- run: echo ${{ inputs.value }}
""",
    )
    _package(
        store_root,
        "general/bundle",
        """version: 1
inputs: [value]
goons:
- import: general/a
  with:
    value: ${{ inputs.value }}
- import: general/b
  with:
    value: ${{ inputs.value }}
""",
    )
    root = _plan(tmp_path, "  - general/bundle\n")
    with pytest.raises(FleetPlanInvalid, match="incompatible child contracts"):
        FleetPlanResolver(goon_templates=GoonTemplateStore(store_root)).resolve(root)


def test_requirement_provenance_survives_nested_composition(tmp_path: Path) -> None:
    store_root = tmp_path / "goons"
    _package(
        store_root,
        "general/leaf",
        _atomic("leaf", requirements="- kind: file_exists\n  path: leaf.marker"),
    )
    _package(
        store_root,
        "general/bundle",
        """version: 1\ngoons:\n  - import: general/leaf\n""",
    )
    root = _plan(tmp_path, "  - import: general/bundle\n")
    plan = FleetPlanResolver(goon_templates=GoonTemplateStore(store_root)).resolve(root)
    leaf = next(item for item in plan.imports if item.name == "general/leaf")
    assert {getattr(item, "path", None) for item in leaf.requirements if hasattr(item, "path")} == {
        "leaf.marker"
    }

    assert plan.requirements == ()

    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    preview = preview_plan(repository, plan)
    leaf_preview = next(item for item in preview.imports if item.name == "general/leaf")
    assert preview.plan_name == plan.name
    assert preview.execution_purpose == "cicd"
    assert preview.repository_execution_mode == "serial"
    assert leaf_preview.applicability == "NOT_APPLICABLE"
    assert "leaf.marker" in leaf_preview.reason
    step = next(
        item for item in preview.steps if item.step_id == "general-bundle.general-leaf.check"
    )
    assert step.disposition.value == "SKIPPED"
    assert step.workspace_source == "repository"
    assert step.cache_source == "empty"
    assert step.egress_mode == "none"


def test_plan_can_project_one_direct_goon_input_without_copying_schema(tmp_path: Path) -> None:
    store_root = tmp_path / "goons"
    _package(
        store_root,
        "general/leaf",
        """version: 1\ninputs:\n  severity:\n    type: choice\n    choices: [low, high]\n    default: low\nsteps:\n  - run: echo ${{ inputs.severity }}\n""",
    )
    root = tmp_path / "plan.yml"
    root.write_text(
        """version: 1\ninputs: [severity]\ngoons:\n  - general/leaf\n""",
        encoding="utf-8",
    )
    plan = FleetPlanResolver(goon_templates=GoonTemplateStore(store_root)).resolve(
        root, inputs={"severity": "high"}
    )
    assert plan.steps[0].argv == ("echo", "high")


def test_plan_projected_input_must_have_one_direct_owner(tmp_path: Path) -> None:
    store_root = tmp_path / "goons"
    for key in ("general/a", "general/b"):
        _package(
            store_root,
            key,
            """version: 1\ninputs:\n  severity:\n    default: low\nsteps:\n  - run: echo ${{ inputs.severity }}\n""",
        )
    root = tmp_path / "plan.yml"
    root.write_text(
        """version: 1\ninputs: [severity]\ngoons:\n  - general/a\n  - general/b\n""",
        encoding="utf-8",
    )
    with pytest.raises(FleetPlanInvalid, match="projected input 'severity' is ambiguous"):
        FleetPlanResolver(goon_templates=GoonTemplateStore(store_root)).resolve(root)


def test_goon_metadata_is_explicit_while_plan_metadata_remains_package_owned() -> None:
    from oh_goon.integrations.fleet_plan_compilation import parse_goon_template, parse_plan_template

    goon = parse_goon_template(
        "version: 1\nname: readable-label\ndescription: Real behavior.\nsteps:\n- run: 'true'\n"
    )
    assert goon.name == "readable-label"
    assert goon.description == "Real behavior."
    with pytest.raises(FleetPlanInvalid, match="unsupported fields: description"):
        parse_plan_template("version: 1\ndescription: legacy label\ngoons:\n- python/quality\n")


def test_goon_positive_tool_authority_compiles_to_exact_step_capabilities() -> None:
    from oh_goon.integrations.fleet_plan_compilation import parse_goon_template

    goon = parse_goon_template(
        """version: 1
name: tooling-tests
description: Run repository tooling tests under strict executable authority.
execution:
  shell: bash
tools:
  allow:
  - sh
steps:
- run: make test-tooling-complete
"""
    )
    step = goon.steps[0]
    assert step.argv == ("make", "test-tooling-complete")
    assert step.capabilities.tools == ("bash", "sh")
    assert "shell" not in step.capabilities.model_dump()


def test_goon_execution_shell_compiles_once_without_duplicate_allow() -> None:
    from oh_goon.integrations.fleet_plan_compilation import parse_goon_template

    goon = parse_goon_template(
        """version: 1
name: tooling-tests
description: Strict tooling.
execution:
  shell: bash
tools:
  allow:
  - sh
steps:
- run: make test-tooling-complete
"""
    )

    assert goon.steps[0].capabilities.tools == ("bash", "sh")


def test_goon_execution_shell_can_be_the_only_authored_tool_authority() -> None:
    from oh_goon.integrations.fleet_plan_compilation import parse_goon_template

    goon = parse_goon_template(
        """version: 1
name: shell-only
description: Strict shell workload.
execution:
  shell: sh
steps:
- run: make shell-only
"""
    )

    assert goon.steps[0].capabilities.tools == ("sh",)


@pytest.mark.parametrize(
    "allow, message",
    [
        (["sh", "bash"], "canonical sorted order"),
        (["bash", "bash"], "must be unique"),
    ],
)
def test_goon_tools_allow_is_canonical_positive_admission(allow: list[str], message: str) -> None:
    from oh_goon.integrations.fleet_plan_compilation import parse_goon_template

    rendered = "\n".join(f"  - {item}" for item in allow)
    with pytest.raises(FleetPlanInvalid, match=message):
        parse_goon_template(
            f"""version: 1
name: tooling-tests
description: Strict tooling.
tools:
  allow:
{rendered}
steps:
- run: make test-tooling-complete
"""
        )


def test_goon_top_level_tools_cannot_merge_with_step_tool_capabilities() -> None:
    from oh_goon.integrations.fleet_plan_compilation import parse_goon_template

    with pytest.raises(FleetPlanInvalid, match="must choose top-level tools.allow"):
        parse_goon_template(
            """version: 1
name: tooling-tests
description: Strict tooling.
tools:
  allow:
  - bash
steps:
- run: make test-tooling-complete
  capabilities:
    tools:
    - sh
"""
        )


def test_goon_top_level_tools_do_not_broaden_multiple_steps() -> None:
    from oh_goon.integrations.fleet_plan_compilation import parse_goon_template

    with pytest.raises(
        FleetPlanInvalid, match="top-level tools.allow is intentionally single-Step"
    ):
        parse_goon_template(
            """version: 1
name: multi-step
description: Multiple Steps need separate authority ownership.
tools:
  allow:
  - sh
steps:
- run: first
- run: second
"""
        )


def test_import_applicability_uses_snapshot_content_not_git_topology(tmp_path: Path) -> None:
    store_root = tmp_path / "goons"
    _package(
        store_root,
        "general/leaf",
        _atomic("leaf", requirements="- kind: file_exists\n  path: marker.txt"),
    )
    root = _plan(tmp_path, "  - import: general/leaf\n")
    plan = FleetPlanResolver(goon_templates=GoonTemplateStore(store_root)).resolve(root)

    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "marker.txt").write_text("ok\n", encoding="utf-8")
    preview = preview_plan(snapshot, plan)

    assert preview.imports[0].applicability == "APPLICABLE"
    assert preview.steps[0].disposition.value == "READY"
