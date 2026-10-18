"""M140: read-only input inspection comes from the canonical resolver source cut."""

from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def test_shipped_plan_inspection_has_truthful_contract_and_owner_paths():
    plans = PlanTemplateStore()
    goons = GoonTemplateStore()
    source = plans.source("advanced/isolated-dataflow")
    resolver = FleetPlanResolver(goon_templates=goons)
    inspected = resolver.inspect_inputs(source.path, containment_root=plans.root.parent.parent)
    definitions = resolver.input_definitions(source.path, containment_root=plans.root.parent.parent)
    assert tuple(item.definition for item in inspected) == definitions
    assert {item.definition.name for item in inspected} == {"tag", "test_timeout_seconds"}
    assert all(item.owner_paths == (("advanced/isolated-dataflow",),) for item in inspected)
    assert all(item.definition.input_type is not None for item in inspected)
    assert all(item.definition.required is not None for item in inspected)


def test_nonprojected_root_inputs_have_no_invented_goon_owner():
    plans = PlanTemplateStore()
    goons = GoonTemplateStore()
    resolver = FleetPlanResolver(goon_templates=goons)
    for key in plans.keys():
        source = plans.source(key)
        if source.compiled.inputs and not source.compiled.projected_inputs:
            inspected = resolver.inspect_inputs(source.path, containment_root=plans.root.parent.parent)
            assert all(item.owner_paths == () for item in inspected)
            return
    # Templates may all use projection; the projected-contract regression above remains valid.


def test_every_shipped_plan_inspection_matches_canonical_materialization():
    """Attack divergent owner projections across the entire public catalog."""
    plans = PlanTemplateStore()
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    for key in plans.keys():
        source = plans.source(key)
        inspected = resolver.inspect_inputs(source.path, containment_root=plans.root.parent.parent)
        canonical = resolver.input_definitions(source.path, containment_root=plans.root.parent.parent)
        assert tuple(item.definition for item in inspected) == canonical, key
        assert len({item.definition.name for item in inspected}) == len(inspected), key
        for item in inspected:
            assert all(path and all(segment for segment in path) for path in item.owner_paths), key
            if item.definition.name in source.compiled.projected_inputs:
                assert item.owner_paths, key
            else:
                assert not item.owner_paths, key


def test_inspection_is_repeatable_and_does_not_mutate_source_files():
    """Attack inspection-only mutation and nondeterministic ownership ordering."""
    plans = PlanTemplateStore()
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    source = plans.source("advanced/isolated-dataflow")
    before = source.path.read_bytes()
    first = resolver.inspect_inputs(source.path, containment_root=plans.root.parent.parent)
    second = resolver.inspect_inputs(source.path, containment_root=plans.root.parent.parent)
    assert first == second
    assert source.path.read_bytes() == before
