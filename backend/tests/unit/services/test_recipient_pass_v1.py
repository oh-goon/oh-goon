"""Recipient-local `pass:` is an authoring-only expansion, not input inheritance."""

from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_plan_compilation import parse_goon_template, parse_plan_template
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def _composite(body: str, *, inputs: str = "[x, y]") -> str:
    return f"version: 1\ninputs: {inputs}\ngoons:\n- import: general/leaf\n{body}"


def _bindings(body: str, *, inputs: str = "[x, y]") -> list[tuple[str, object]]:
    item = parse_goon_template(_composite(body, inputs=inputs)).steps[0]
    return [(binding.name, binding.value) for binding in item.inputs]


@pytest.mark.parametrize("forward", ["  pass: [x]\n", "  pass:\n    - x\n"])
def test_pass_formats_normalize_to_exact_existing_input_binding(forward: str) -> None:
    assert _bindings(forward) == [("x", "${{ inputs.x }}")]


def test_pass_order_and_with_are_deterministic_independent_of_yaml_key_order() -> None:
    first = "  pass: [y, x]\n  with:\n    a: fixed\n    b: ${{ inputs.x }}\n"
    second = "  with:\n    a: fixed\n    b: ${{ inputs.x }}\n  pass: [y, x]\n"
    expected = [("y", "${{ inputs.y }}"), ("x", "${{ inputs.x }}"), ("a", "fixed"), ("b", "${{ inputs.x }}")]
    assert _bindings(first) == _bindings(second) == expected


def test_legacy_with_and_bare_imports_remain_unchanged() -> None:
    assert _bindings("  with:\n    x: ${{ inputs.x }}\n") == [("x", "${{ inputs.x }}")]
    compiled = parse_goon_template("version: 1\ngoons: [general/leaf]\n")
    assert compiled.steps[0].inputs == ()


@pytest.mark.parametrize("value", ["[]", "null", "x", "{x: 5}", "[all]", "[inputs]", "['*']", "[true]", "[42]", "[[x]]", "[' x']", "['x!']", "[x, x]"])
def test_bad_pass_forms_fail_at_compilation(value: str) -> None:
    with pytest.raises(FleetPlanInvalid, match=r"pass|input name"):
        parse_goon_template(_composite(f"  pass: {value}\n"))


def test_duplicate_name_in_pass_is_rejected_without_precedence() -> None:
    with pytest.raises(FleetPlanInvalid, match=r"duplicate pass input 'x'"):
        parse_goon_template(_composite("  pass: [x, x]\n"))


def test_pass_with_collision_fails_even_if_values_equal() -> None:
    with pytest.raises(FleetPlanInvalid, match=r"duplicate child input binding 'x'"):
        parse_goon_template(_composite("  pass: [x]\n  with:\n    x: ${{ inputs.x }}\n"))


def test_pass_parent_must_declare_name() -> None:
    with pytest.raises(FleetPlanInvalid, match=r"Composite inputs does not declare 'orphan'"):
        parse_goon_template(_composite("  pass: [orphan]\n"))


def test_pass_is_composite_only_and_not_plan_projection() -> None:
    with pytest.raises(FleetPlanInvalid, match="Composite Goon imports"):
        parse_plan_template("version: 1\ninputs: [x]\ngoons:\n- import: general/leaf\n  pass: [x]\n")
    with pytest.raises(FleetPlanInvalid, match="unsupported"):
        parse_goon_template("version: 1\npass: [x]\ngoons: [general/leaf]\n")
    with pytest.raises(FleetPlanInvalid):
        parse_goon_template("version: 1\nsteps:\n- run: echo ok\n  pass: [x]\n")


def test_extra_import_field_still_fails() -> None:
    with pytest.raises(FleetPlanInvalid, match="unsupported fields: secret"):
        parse_goon_template(_composite("  pass: [x]\n  secret: accidental\n"))


def test_invalid_yaml_duplicate_pass_key_fails_before_model_build() -> None:
    with pytest.raises(FleetPlanInvalid, match="duplicate key 'pass'"):
        parse_goon_template(_composite("  pass: [x]\n  pass: [y]\n"))


def test_pass_keeps_failure_impact_and_repeated_occurrence_identity() -> None:
    compiled = parse_goon_template("""version: 1
inputs: [x]
goons:
- import: general/leaf
  failure_impact: non_blocking
  pass: [x]
- import: general/leaf
  pass: [x]
""")
    assert [step.step_id for step in compiled.steps] == ["general-leaf", "general-leaf-2"]
    assert all([binding.name for binding in step.inputs] == ["x"] for step in compiled.steps)
    assert str(compiled.steps[0].failure_impact).lower().endswith("non_blocking")


def test_maximum_32_combined_bindings_enforced_by_compiled_model() -> None:
    names = [f"input_{i}" for i in range(32)]
    inputs = "[" + ", ".join(names) + "]"
    assert len(_bindings("  pass: [" + ", ".join(names) + "]\n", inputs=inputs)) == 32
    with pytest.raises(FleetPlanInvalid, match="32|too_long|maximum"):
        _bindings("  pass: [" + ", ".join(names) + "]\n  with:\n    extra: fixed\n", inputs=inputs)


def _package(root: Path, key: str, content: str) -> None:
    category, name = key.split("/")
    directory = root / category / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "goon.yml").write_text(content, encoding="utf-8")
    (directory / "README.md").write_text("# " + key + "\n", encoding="utf-8")


def _leaf(*, child_input: str = "x", input_type: str = "string") -> str:
    return f"""version: 1
inputs:
  {child_input}:
    type: {input_type}
    default: good
steps:
- run: echo ${{{{ inputs.{child_input} }}}}
"""


def _resolve(tmp_path: Path, *, parent: str, child: str | None = None, plan_inputs: str = ""):
    root = tmp_path / "goons"
    _package(root, "general/leaf", child if child is not None else _leaf())
    _package(root, "general/bundle", parent)
    plan = tmp_path / "plan.yml"
    plan.write_text("version: 1\n" + plan_inputs + "goons:\n- general/bundle\n", encoding="utf-8")
    return FleetPlanResolver(goon_templates=GoonTemplateStore(root)).resolve(plan)


def test_same_name_parent_value_is_forwarded_to_exact_recipient(tmp_path: Path) -> None:
    plan = _resolve(
        tmp_path,
        parent="version: 1\ninputs: [x]\ngoons:\n- import: general/leaf\n  pass: [x]\n",
        plan_inputs="inputs: [x]\n",
    )
    assert plan.steps[0].argv == ("echo", "good")


def test_wrong_child_input_rejected_by_source_graph(tmp_path: Path) -> None:
    with pytest.raises(FleetPlanInvalid, match="x|input|own"):
        _resolve(
            tmp_path,
            parent="version: 1\ninputs: [x]\ngoons:\n- import: general/leaf\n  pass: [x]\n",
            child=_leaf(child_input="other"),
        )


def test_no_ambient_forwarding_to_matching_sibling(tmp_path: Path) -> None:
    root = tmp_path / "goons"
    _package(root, "general/leaf", _leaf())
    _package(root, "general/second", _leaf())
    _package(root, "general/bundle", """version: 1
inputs: [x]
goons:
- import: general/leaf
  pass: [x]
- import: general/second
""")
    plan = tmp_path / "plan.yml"
    plan.write_text("version: 1\ninputs: [x]\ngoons: [general/bundle]\n", encoding="utf-8")
    # A missing edge is never silently synthesized; default on the second child
    # does not imply a parent pass binding at its import occurrence.
    parsed = GoonTemplateStore(root).source("general/bundle").compiled
    assert len(parsed.steps[0].inputs) == 1
    assert parsed.steps[1].inputs == ()


def test_both_recipients_have_separate_edges(tmp_path: Path) -> None:
    root = tmp_path / "goons"
    for key in ("general/leaf", "general/second"):
        _package(root, key, _leaf())
    _package(root, "general/bundle", """version: 1
inputs: [x]
goons:
- import: general/leaf
  pass: [x]
- import: general/second
  pass: [x]
""")
    plan = tmp_path / "plan.yml"
    plan.write_text("version: 1\ninputs: [x]\ngoons: [general/bundle]\n", encoding="utf-8")
    resolved = FleetPlanResolver(goon_templates=GoonTemplateStore(root)).resolve(plan)
    assert [step.argv for step in resolved.steps] == [("echo", "good"), ("echo", "good")]


@pytest.mark.parametrize("key", ["python/quality", "oci/publish-image", "kubernetes/release"])
def test_shipped_pass_routes_use_existing_binding_models_only(key: str) -> None:
    template = GoonTemplateStore().source(key).compiled
    assert template.projected_inputs
    assert all(binding.value is not None for step in template.steps for binding in step.inputs)
    assert all(not hasattr(binding, "pass") for step in template.steps for binding in step.inputs)


def test_unmodified_security_composite_has_no_pass() -> None:
    template = GoonTemplateStore().source("security/cve-combined").compiled
    assert all(step.inputs == () for step in template.steps)


def test_inspection_uses_exact_existing_owner_paths() -> None:
    store = PlanTemplateStore()
    source = store.source("python/quality")
    results = FleetPlanResolver(goon_templates=GoonTemplateStore()).inspect_inputs(
        source.path, containment_root=store.root.parent.parent
    )
    assert len(results) == 1
    assert results[0].definition.name == "coverage_threshold"
    assert any("python/test" in owner for path in results[0].owner_paths for owner in path)


def test_incompatible_multi_recipient_pass_contracts_fail_before_work(tmp_path: Path) -> None:
    root = tmp_path / "goons"
    _package(root, "general/leaf", _leaf())
    _package(root, "general/second", """version: 1
inputs:
  x:
    type: choice
    choices: [small, large]
    default: small
steps:
- run: echo ${{ inputs.x }}
""")
    _package(root, "general/bundle", """version: 1
inputs: [x]
goons:
- import: general/leaf
  pass: [x]
- import: general/second
  pass: [x]
""")
    path = tmp_path / "plan.yml"
    path.write_text("version: 1\ninputs: [x]\ngoons: [general/bundle]\n")
    with pytest.raises(FleetPlanInvalid, match="incompatible|same|contract|conflict"):
        FleetPlanResolver(goon_templates=GoonTemplateStore(root)).resolve(path)


def test_nested_composites_require_each_explicit_recipient_edge(tmp_path: Path) -> None:
    root = tmp_path / "goons"
    _package(root, "general/leaf", _leaf())
    _package(root, "general/middle", """version: 1
inputs: [x]
goons:
- import: general/leaf
  pass: [x]
""")
    outer = """version: 1
inputs: [x]
goons:
- import: general/middle
  pass: [x]
"""
    _package(root, "general/outer", outer)
    path = tmp_path / "plan.yml"
    path.write_text("version: 1\ninputs: [x]\ngoons: [general/outer]\n")
    store = GoonTemplateStore(root)
    assert FleetPlanResolver(goon_templates=store).resolve(path).steps[0].argv == ("echo", "good")
    _package(root, "general/outer", outer.replace("  pass: [x]\n", ""))
    with pytest.raises(FleetPlanInvalid, match="explicitly forwarded"):
        FleetPlanResolver(goon_templates=store).resolve(path)


def test_renamed_input_uses_existing_with_and_pass_cannot_rename(tmp_path: Path) -> None:
    parent = """version: 1
inputs: [x]
goons:
- import: general/leaf
  with:
    child_name: ${{ inputs.x }}
"""
    resolved = _resolve(tmp_path, parent=parent, child=_leaf(child_input="child_name"))
    assert resolved.steps[0].argv == ("echo", "good")
    with pytest.raises(FleetPlanInvalid, match="Composite inputs does not declare"):
        _resolve(
            tmp_path,
            parent=parent.replace("  with:\n    child_name: ${{ inputs.x }}", "  pass: [child_name]"),
            child=_leaf(child_input="child_name"),
        )


def test_rejected_pass_never_reaches_runtime_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    # Reject before creating any graph/run object. Regression against a future
    # 'helpful' fallback from invalid pass into runtime admission or execution.
    from oh_goon.services import fleet_plan_resolver

    calls: list[object] = []
    monkeypatch.setattr(
        fleet_plan_resolver.FleetPlanResolver,
        "resolve",
        lambda self, *args, **kwargs: calls.append(args),
    )
    with pytest.raises(FleetPlanInvalid, match="duplicate child input binding"):
        parse_goon_template(_composite("  pass: [x]\n  with:\n    x: same\n"))
    assert calls == []
