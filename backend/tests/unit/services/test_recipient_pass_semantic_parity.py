"""Golden parity for v1 same-name recipient forwarding against frozen M219 YAML."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from oh_goon.integrations.fleet_plan_compilation import parse_goon_template
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "backend/tests/fixtures/recipient_pass_v1"
SHIPPED_GOONS = ROOT / "templates/goons"

KEYS = (
    ("python/quality", "python-quality-legacy.yml", 1),
    ("oci/publish-image", "oci-publish-image-legacy.yml", 6),
    ("kubernetes/release", "kubernetes-release-legacy.yml", 9),
)


def _binding_map(step):
    return {binding.name: binding.model_dump(mode="json") for binding in step.inputs}


@pytest.mark.parametrize("key,fixture,edges", KEYS)
def test_compiled_templates_have_exact_semantic_binding_parity(
    key: str, fixture: str, edges: int
) -> None:
    legacy = parse_goon_template(
        (FIXTURES / fixture).read_text(encoding="utf-8"),
        source=str(FIXTURES / fixture),
        expected_name=key.split("/")[1],
    )
    authored = GoonTemplateStore().source(key).compiled
    old_bytes = (FIXTURES / fixture).read_bytes()
    new_bytes = (SHIPPED_GOONS / key / "goon.yml").read_bytes()
    assert hashlib.sha256(old_bytes).digest() != hashlib.sha256(new_bytes).digest()
    assert legacy.projected_inputs == authored.projected_inputs
    assert legacy.outputs == authored.outputs
    assert legacy.requirements == authored.requirements
    assert legacy.execution == authored.execution
    assert legacy.env == authored.env
    assert len(legacy.steps) == len(authored.steps)
    for previous, now in zip(legacy.steps, authored.steps, strict=False):
        assert (previous.goon, previous.step_id, previous.failure_impact) == (
            now.goon,
            now.step_id,
            now.failure_impact,
        )
        assert _binding_map(previous) == _binding_map(now)
    assert (
        sum(
            binding.value.startswith("${{ inputs.")
            for step in authored.steps
            for binding in step.inputs
            if isinstance(binding.value, str)
        )
        == edges
    )
    assert (
        parse_goon_template(
            (SHIPPED_GOONS / key / "goon.yml").read_text(), expected_name=key.split("/")[1]
        )
        == authored
    )


@pytest.mark.parametrize("key,fixture,_", KEYS)
def test_source_graph_inspection_preserves_input_owner_paths_in_isolated_roots(
    key: str, fixture: str, _: int, tmp_path: Path
) -> None:
    observations = []
    for flavor in ("legacy", "new"):
        source_root = tmp_path / flavor
        goons = source_root / "goons"
        shutil.copytree(SHIPPED_GOONS, goons)
        if flavor == "legacy":
            (goons / key / "goon.yml").write_bytes((FIXTURES / fixture).read_bytes())
        plan = source_root / "plan.yml"
        # A minimal standalone caller projecting the Composite's entire public interface.
        names = GoonTemplateStore(goons).source(key).compiled.projected_inputs
        plan.write_text(
            "version: 1\ninputs: [" + ", ".join(names) + "]\ngoons: [" + key + "]\n",
            encoding="utf-8",
        )
        inspected = FleetPlanResolver(goon_templates=GoonTemplateStore(goons)).inspect_inputs(
            plan, containment_root=source_root
        )
        observations.append(tuple((item.definition, item.owner_paths) for item in inspected))
    assert observations[0] == observations[1]


def test_unmigrated_composite_and_plan_level_producer_binding_still_use_with() -> None:
    security = (SHIPPED_GOONS / "security/cve-combined/goon.yml").read_text()
    assert "pass:" not in security
    release_plan = (ROOT / "templates/plans/oci/release-deploy/plan.yml").read_text()
    assert "pass:" not in release_plan
    assert "goons.oci-publish-image.outputs.OCI_REFERENCE" in release_plan
