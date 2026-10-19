"""M135: derive input/identity documentation contracts from shipped OCI sources."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_oci_steps import (
    ResolvedOciBuildStep,
    ResolvedOciPublishStep,
)
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md"
OCI_PLANS = ROOT / "templates/plans/oci"
OCI_GOONS = ROOT / "templates/goons/oci"


def _load(relative: str) -> dict[str, Any]:
    payload = yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _registry_route_errors(
    root: Path,
    image: dict[str, Any],
    publish: dict[str, Any],
    composite: dict[str, Any],
) -> tuple[str, ...]:
    """Check the real compiler's resolved endpoint and producer relationships."""
    goons = root / "goons"
    for name, document in (
        ("image", image),
        ("publish", publish),
        ("publish-image", composite),
    ):
        package = goons / "oci" / name
        package.mkdir(parents=True)
        (package / "goon.yml").write_text(yaml.safe_dump(document))
        (package / "README.md").write_text(f"# oci/{name}\n")
    plan = root / "plan.yml"
    plan.write_text(
        "version: 1\nexecution: {purpose: cicd}\ngoons: [oci/publish-image]\n"
    )
    try:
        resolved = FleetPlanResolver(goon_templates=GoonTemplateStore(goons)).resolve(
            plan,
            inputs={
                "base_registry_hostname": "base.example.com",
                "publish_registry_hostname": "publish.example.com",
                "publish_repository": "team/image",
            },
        )
    except FleetPlanInvalid as exc:
        return (str(exc),)
    build, pushed = resolved.steps
    assert isinstance(build, ResolvedOciBuildStep)
    assert isinstance(pushed, ResolvedOciPublishStep)
    problems = []
    if tuple(item.hostname for item in build.registries) != ("base.example.com",):
        problems.append("builder registry differs from its selected input")
    if pushed.target.registry.hostname != "publish.example.com":
        problems.append("publisher registry differs from its selected input")
    if pushed.target.repository != "team/image":
        problems.append("publisher repository differs from its selected input")
    if (
        pushed.workspace.source.kind != "step"
        or pushed.workspace.source.step_id != build.step_id
    ):
        problems.append("publisher must consume the builder workspace")
    return tuple(problems)


def _contracts() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        _load("templates/goons/oci/image/goon.yml"),
        _load("templates/goons/oci/publish/goon.yml"),
        _load("templates/goons/oci/publish-image/goon.yml"),
    )


def test_m135_shipped_registry_and_workspace_routes_have_one_authority(
    tmp_path: Path,
) -> None:
    assert not _registry_route_errors(tmp_path, *_contracts())


@pytest.mark.parametrize(
    ("which", "field"),
    (
        (0, "registry"),
        (1, "registry"),
        (2, "forward-base"),
        (2, "forward-publish"),
        (2, "workspace"),
    ),
)
def test_m135_mutated_registry_or_workspace_wiring_is_detected(
    tmp_path: Path, which: int, field: str
) -> None:
    inputs = [copy.deepcopy(value) for value in _contracts()]
    if field == "registry" and which == 0:
        inputs[0]["steps"][0]["registries"][0]["hostname"] = (
            "${{ inputs.publish_registry_hostname }}"
        )
    elif field == "registry":
        inputs[1]["steps"][0]["target"]["registry"]["hostname"] = (
            "${{ inputs.base_registry_hostname }}"
        )
    elif field == "forward-base":
        # Change only this recipient; the parent name remains declared.
        inputs[2]["goons"][0] = {"import": "oci/image"}
        inputs[2]["goons"][0].setdefault("with", {})["base_registry_hostname"] = (
            "${{ inputs.publish_registry_hostname }}"
        )
    elif field == "forward-publish":
        inputs[2]["goons"][1].setdefault("with", {})["publish_registry_hostname"] = (
            "${{ inputs.base_registry_hostname }}"
        )
    else:
        inputs[2]["goons"][1]["with"]["source_workspace"] = "some-local-file"
    assert _registry_route_errors(tmp_path, *inputs)


def test_m135_docs_cover_exact_shipped_caller_inputs_without_a_maximum() -> None:
    document = GUIDE.read_text(encoding="utf-8")
    plan = _load("templates/plans/oci/release-deploy/plan.yml")
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    names = {
        item.definition.name
        for item in resolver.inspect_inputs(OCI_PLANS / "release-deploy/plan.yml")
    }
    assert all(
        re.search(rf"^\| `{re.escape(name)}` \| A:", document, re.M) for name in names
    )
    assert "no arbitrary maximum input count" in document
    assert "No B/C/D input" in document
    assert (
        "${{ goons.oci-publish-image.outputs.OCI_REFERENCE }}"
        == (plan["goons"][-1]["with"]["image"])
    )
    assert "image" not in names
    assert "source_workspace" not in names
    for name in ("oci/release", "oci/publish-image", "oci/release-deploy"):
        public = {
            item.definition.name
            for item in resolver.inspect_inputs(
                ROOT / "templates/plans" / name / "plan.yml"
            )
        }
        assert {
            "base_registry_hostname",
            "publish_registry_hostname",
            "publish_repository",
        } <= public
        assert "source_workspace" not in public


def test_m135_oci_documentation_links_and_examples_do_not_go_stale() -> None:
    pages = (
        ROOT / "templates/plans/README.md",
        ROOT / "templates/goons/README.md",
        *(path / "README.md" for path in OCI_PLANS.iterdir() if path.is_dir()),
        *(path / "README.md" for path in OCI_GOONS.iterdir() if path.is_dir()),
        GUIDE,
    )
    for page in pages:
        text = page.read_text(encoding="utf-8")
        for link in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", text):
            if not link.startswith(("http://", "https://", "mailto:")):
                assert (page.parent / link).resolve().is_file(), (page, link)
    authoring = (ROOT / "templates/plans/README.md").read_text(encoding="utf-8")
    assert "${{ goons.oci-publish-image.outputs.OCI_REFERENCE }}" in authoring
    assert "- registry_hostname" not in authoring
    assert authoring.count("```") % 2 == 0
