"""T1: one inspected source cut and no duplicated input-schema owner."""

from __future__ import annotations

import hashlib

from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import FleetPlanService


def test_inspection_and_root_fingerprint_share_one_source_graph(monkeypatch):
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    source = PlanTemplateStore().source("python/quality")
    original = resolver.analyze_source
    graphs = []

    def captured(*args, **kwargs):
        graph = original(*args, **kwargs)
        graphs.append(graph)
        return graph

    monkeypatch.setattr(resolver, "analyze_source", captured)
    inspected, digest = resolver.inspect_inputs_with_source(source.path)
    assert len(graphs) == 1
    assert digest == graphs[0].root.source_sha256
    assert digest == hashlib.sha256(source.path.read_bytes()).hexdigest()
    assert inspected[0].owner_paths == (("python/quality", "python/test"),)


def test_service_adapter_forwards_to_canonical_resolver_without_duplicate_read():
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    service = FleetPlanService.__new__(FleetPlanService)
    service._resolver = resolver
    source = PlanTemplateStore().source("oci/release-deploy")
    assert service.inspect_inputs_with_source(source.path) == resolver.inspect_inputs_with_source(
        source.path
    )
