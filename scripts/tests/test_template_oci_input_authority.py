"""M135: derive input/identity documentation contracts from shipped OCI sources."""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs/development/TEMPLATE_OCI_INPUT_AUTHORITY.md"
OCI_PLANS = ROOT / "templates/plans/oci"
OCI_GOONS = ROOT / "templates/goons/oci"


def _load(relative: str) -> dict:
    return yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))


def _authored_bindings(import_item: dict) -> dict[str, object]:
    """Expand explicit same-name pass edges solely for static contract comparison.

    The production compiler remains the authority for grammar and normalization.
    This test view must reject ambiguity instead of silently overwriting either form.
    """
    forwarded = dict(import_item.get("with", {}))
    for name in import_item.get("pass", ()):
        if name in forwarded:
            raise ValueError("pass and with both provide the same recipient")
        forwarded[name] = "${{ inputs." + name + " }}"
    return forwarded


def _registry_route_errors(
    image: dict, publish: dict, composite: dict
) -> tuple[str, ...]:
    """Static contract review only; production resolution is tested separately."""
    problems = []
    image_step = image["steps"][0]
    publish_step = publish["steps"][0]
    forwarded = {
        item["import"]: _authored_bindings(item) for item in composite["goons"]
    }
    if image_step["registries"] != [
        {"hostname": "${{ inputs.base_registry_hostname }}"}
    ]:
        problems.append("source registry must belong to OCI builder")
    if publish_step["target"]["registry"]["hostname"] != (
        "${{ inputs.publish_registry_hostname }}"
    ):
        problems.append("destination registry must belong to OCI publisher")
    if publish_step["target"]["repository"] != "${{ inputs.publish_repository }}":
        problems.append("publisher repository must be independent")
    if forwarded["oci/image"].get("base_registry_hostname") != (
        "${{ inputs.base_registry_hostname }}"
    ):
        problems.append("base registry forwarder was rewritten")
    if forwarded["oci/publish"].get("publish_registry_hostname") != (
        "${{ inputs.publish_registry_hostname }}"
    ):
        problems.append("publish registry forwarder was rewritten")
    if forwarded["oci/publish"].get("publish_repository") != (
        "${{ inputs.publish_repository }}"
    ):
        problems.append("publish repository forwarder was rewritten")
    if forwarded["oci/publish"].get("source_workspace") != (
        "${{ goons.oci-image.workspace }}"
    ):
        problems.append("published workspace must come from builder")
    return tuple(problems)


def _contracts() -> tuple[dict, dict, dict]:
    return (
        _load("templates/goons/oci/image/goon.yml"),
        _load("templates/goons/oci/publish/goon.yml"),
        _load("templates/goons/oci/publish-image/goon.yml"),
    )


def test_m135_shipped_registry_and_workspace_routes_have_one_authority() -> None:
    assert not _registry_route_errors(*_contracts())


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
    which: int, field: str
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
        inputs[2]["goons"][0]["pass"].remove("base_registry_hostname")
        inputs[2]["goons"][0].setdefault("with", {})["base_registry_hostname"] = (
            "${{ inputs.publish_registry_hostname }}"
        )
    elif field == "forward-publish":
        inputs[2]["goons"][1]["pass"].remove("publish_registry_hostname")
        inputs[2]["goons"][1].setdefault("with", {})["publish_registry_hostname"] = (
            "${{ inputs.base_registry_hostname }}"
        )
    else:
        inputs[2]["goons"][1]["with"]["source_workspace"] = "some-local-file"
    assert _registry_route_errors(*inputs)


def test_pass_and_with_cannot_silently_override_one_recipient() -> None:
    import_item = {
        "import": "oci/image",
        "pass": ["base_registry_hostname"],
        "with": {"base_registry_hostname": "unexpected"},
    }
    with pytest.raises(ValueError, match="both provide the same recipient"):
        _authored_bindings(import_item)


def test_authored_bindings_preserve_workspace_edge_and_exact_recipient() -> None:
    composite = _contracts()[2]
    image = _authored_bindings(composite["goons"][0])
    publish = _authored_bindings(composite["goons"][1])
    assert "publish_registry_hostname" not in image
    assert "base_registry_hostname" not in publish
    assert publish["source_workspace"] == "${{ goons.oci-image.workspace }}"


def test_m135_docs_cover_exact_shipped_caller_inputs_without_a_maximum() -> None:
    document = GUIDE.read_text(encoding="utf-8")
    plan = _load("templates/plans/oci/release-deploy/plan.yml")
    assert all(
        re.search(rf"^\| `{re.escape(name)}` \| A:", document, re.M)
        for name in plan["inputs"]
    )
    assert "no arbitrary maximum input count" in document
    assert "No B/C/D input" in document
    assert (
        "${{ goons.oci-publish-image.outputs.OCI_REFERENCE }}"
        == (plan["goons"][-1]["with"]["image"])
    )
    assert "image" not in plan["inputs"]
    assert "source_workspace" not in plan["inputs"]
    assert (
        "source_workspace"
        not in _load("templates/plans/oci/publish-image/plan.yml")["inputs"]
    )
    for name in ("oci/release", "oci/publish-image", "oci/release-deploy"):
        selected = _load(f"templates/plans/{name}/plan.yml")
        assert "base_registry_hostname" in selected["inputs"]
        assert "publish_registry_hostname" in selected["inputs"]
        assert "publish_repository" in selected["inputs"]


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
