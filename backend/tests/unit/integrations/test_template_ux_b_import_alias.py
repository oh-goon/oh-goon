"""UX-B: import aliases name exact occurrences inside the existing compiler."""

from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_plan_compilation import parse_goon_template, parse_plan_template
from oh_goon.integrations.fleet_plan_source_snapshot import read_fleet_plan_source
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def _plan(body: str) -> str:
    return "version: 1\ngoons:\n" + body


def _composite(body: str) -> str:
    return "version: 1\ngoons:\n" + body


def test_explicit_occurrence_alias_in_plan_without_runtime_metadata() -> None:
    result = parse_plan_template(
        _plan("- import: general/leaf\n  as: first\n- import: general/leaf\n  as: second\n")
    )
    assert [s.step_id for s in result.steps] == ["first", "second"]
    assert [s.goon for s in result.steps] == ["general/leaf"] * 2
    assert all(not hasattr(s, "as") for s in result.steps)


def test_composite_alias_and_pass_are_one_import_occurrence() -> None:
    result = parse_goon_template(
        "version: 1\ninputs: [x]\ngoons:\n- import: general/leaf\n  as: named\n  pass: [x]\n"
    )
    assert result.steps[0].step_id == "named"
    assert [(b.name, b.value) for b in result.steps[0].inputs] == [("x", "${{ inputs.x }}")]


def test_legacy_generated_ids_not_rewritten_when_neighbors_have_aliases() -> None:
    result = parse_plan_template(
        _plan("- general/leaf\n- import: general/leaf\n  as: named\n- general/leaf\n")
    )
    assert [s.step_id for s in result.steps] == ["general-leaf", "named", "general-leaf-3"]


@pytest.mark.parametrize(
    "alias",
    [
        "",
        "Null",
        "UPPER",
        "two words",
        "_hidden",
        "a_b",
        "abc-",
        "-abc",
        "1abc",
        "goons",
        "steps",
        "inputs",
        "outputs",
        "self",
        "a" * 41,
        "null",
        "true",
        "[a]",
        "{a: b}",
    ],
)
def test_invalid_alias_rejected_before_resolution(alias: str) -> None:
    with pytest.raises(FleetPlanInvalid, match="as:|alias"):
        parse_plan_template(_plan(f"- import: general/leaf\n  as: '{alias}'\n"))


@pytest.mark.parametrize("value", ["null", "true", "1", "[x]", "{x: y}"])
def test_non_string_alias_is_rejected(value: str) -> None:
    with pytest.raises(FleetPlanInvalid, match="as:"):
        parse_plan_template(_plan(f"- import: general/leaf\n  as: {value}\n"))


def test_duplicate_explicit_alias_rejected_without_suffix() -> None:
    with pytest.raises(FleetPlanInvalid, match="duplicate explicit"):
        parse_plan_template(
            _plan("- import: general/leaf\n  as: one\n- import: general/other\n  as: one\n")
        )


@pytest.mark.parametrize(
    "body",
    [
        "- general/leaf\n- import: general/other\n  as: general-leaf\n",
        "- import: general/other\n  as: general-leaf\n- general/leaf\n",
        "- import: general/leaf\n  as: general-leaf-2\n- general/leaf\n- general/leaf\n",
    ],
)
def test_alias_cannot_collide_with_generated_step_id_in_any_order(body: str) -> None:
    with pytest.raises(FleetPlanInvalid, match="collides"):
        parse_plan_template(_plan(body))


def test_ambiguous_generated_collision_rejected_not_silently_merged() -> None:
    with pytest.raises(FleetPlanInvalid, match="collides"):
        parse_plan_template(_plan("- general/leaf-one\n- general-leaf/one\n"))


def test_explicit_alias_not_allowed_on_atomic_step() -> None:
    with pytest.raises(FleetPlanInvalid):
        parse_goon_template("version: 1\nsteps:\n- run: echo ok\n  as: accidental\n")


def test_import_alias_cannot_be_outside_import_mapping() -> None:
    with pytest.raises(FleetPlanInvalid):
        parse_plan_template(_plan("- as: bad\n"))


def test_unknown_fields_and_duplicate_yaml_alias_are_still_rejected() -> None:
    with pytest.raises(FleetPlanInvalid, match="unsupported fields"):
        parse_plan_template(_plan("- import: general/leaf\n  as: foo\n  secret: nope\n"))
    with pytest.raises(FleetPlanInvalid, match="duplicate key"):
        parse_plan_template(_plan("- import: general/leaf\n  as: foo\n  as: bar\n"))


def test_original_source_snapshot_positions_follow_explicit_ids(tmp_path: Path) -> None:
    source = tmp_path / "plan.yml"
    source.write_text(
        _plan("- import: general/leaf\n  as: one\n- import: general/leaf\n  as: two\n")
    )
    snapshot = read_fleet_plan_source(source, kind="plan")
    assert tuple(snapshot.step_positions) == ("one", "two")
    assert snapshot.step_positions["one"].line == 3
    assert snapshot.step_positions["two"].line == 5


def test_existing_source_graph_resolves_named_occurrences_with_no_new_runner(
    tmp_path: Path,
) -> None:
    store = tmp_path / "goons" / "general" / "leaf"
    store.mkdir(parents=True)
    (store / "goon.yml").write_text("version: 1\nsteps:\n- run: echo ok\n")
    (store / "README.md").write_text("# leaf\n")
    plan = tmp_path / "plan.yml"
    plan.write_text(
        _plan("- import: general/leaf\n  as: left\n- import: general/leaf\n  as: right\n")
    )
    resolved = FleetPlanResolver(goon_templates=GoonTemplateStore(tmp_path / "goons")).resolve(plan)
    assert len(resolved.steps) == 2
    assert resolved.steps[0].step_id.startswith("left.")
    assert resolved.steps[1].step_id.startswith("right.")
    assert [i.import_id for i in resolved.imports] == ["left", "right"]


def test_alias_output_reference_uses_canonical_producer_identity(tmp_path: Path) -> None:
    provider = tmp_path / "goons"
    source = provider / "general" / "producer"
    source.mkdir(parents=True)
    (source / "README.md").write_text("# producer\n")
    (source / "goon.yml").write_text("""version: 1
outputs:
  RESULT: ${{ steps.publish.outputs.RESULT }}
steps:
- id: publish
  run: echo ready
  outputs:
    RESULT: result.txt
""")
    consumer = provider / "general" / "consumer"
    consumer.mkdir(parents=True)
    (consumer / "README.md").write_text("# consumer\n")
    (consumer / "goon.yml").write_text("""version: 1
inputs:
  required_output: null
steps:
- run: echo ${{ inputs.required_output }}
""")
    plan = tmp_path / "plan.yml"
    plan.write_text("""version: 1
goons:
- import: general/producer
  as: created
- import: general/consumer
  with:
    required_output: ${{ goons.created.outputs.RESULT }}
""")
    resolved = FleetPlanResolver(goon_templates=GoonTemplateStore(provider)).resolve(plan)
    assert [i.import_id for i in resolved.imports] == ["created", "general-consumer"]
    assert resolved.steps[-1].argv[-1] == "${{ steps.created.publish.outputs.RESULT }}"
    plan.write_text(
        plan.read_text().replace("goons.created.outputs", "goons.general-producer.outputs")
    )
    with pytest.raises(FleetPlanInvalid):
        FleetPlanResolver(goon_templates=GoonTemplateStore(provider)).resolve(plan)


def test_native_development_source_gate_selects_ux_b_after_ux_a() -> None:
    script = (
        Path(__file__).resolve().parents[4] / "scripts" / "qualify_b12_development_wsl.sh"
    ).read_text()
    assert script.index("embedded='.checkpoint/UX_A_SOURCE.sha256'") < script.index(
        "embedded='.checkpoint/UX_B_SOURCE.sha256'"
    )
    assert 'source_transport_audit --baseline "$embedded"' in script


def test_legacy_no_alias_source_positions_remain_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "plan.yml"
    source.write_text(_plan("- general/leaf\n- import: general/leaf\n"))
    assert dict(read_fleet_plan_source(source, kind="plan").step_positions) == {}
