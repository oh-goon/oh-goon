"""M133: protect author-facing input/transport documentation from catalog drift."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs/development/TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md"
LINKED = (
    ROOT / "docs/README.md",
    ROOT / "docs/CI_CD_CONTRACT.md",
    ROOT / "templates/README.md",
    ROOT / "templates/plans/README.md",
    ROOT / "templates/goons/README.md",
)
REFERENCE = "TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md"


def _markdown_links(markdown: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", markdown))


def _issues_for_navigation(path: Path, markdown: str) -> list[str]:
    problems = []
    if path != GUIDE and REFERENCE not in markdown:
        problems.append(f"missing M133 guide navigation in {path.name}")
    for reference in _markdown_links(markdown):
        if reference.startswith(("https://", "http://", "mailto:")):
            continue
        if not (path.parent / reference).resolve().is_file():
            problems.append(f"broken local documentation link: {path.name}: {reference}")
    return problems


def _catalog(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def test_m133_navigation_has_no_missing_or_dangling_local_links() -> None:
    for path in (GUIDE, *LINKED):
        text = path.read_text(encoding="utf-8")
        assert not _issues_for_navigation(path, text), path


def test_m133_navigation_negative_malformed_link_and_missing_guide() -> None:
    page = ROOT / "templates/plans/README.md"
    assert "missing M133 guide navigation" in _issues_for_navigation(page, "# Shipped Plans")[0]
    assert any(
        "broken local documentation link" in problem
        for problem in _issues_for_navigation(page, f"[{REFERENCE}](missing-contract.md)")
    )


def test_m133_documented_projection_remains_owned_by_leaf_goon() -> None:
    plan = _catalog("templates/plans/python/quality/plan.yml")
    composite = _catalog("templates/goons/python/quality/goon.yml")
    atomic = _catalog("templates/goons/python/test/goon.yml")
    assert plan["inputs"] == ["coverage_threshold"]
    assert "coverage_threshold" in composite["inputs"]
    assert composite["goons"][-1]["import"] == "python/test"
    assert composite["goons"][-1]["pass"] == ["coverage_threshold"]
    assert "coverage_threshold" not in composite["goons"][-1].get("with", {})
    assert atomic["inputs"]["coverage_threshold"]["type"] == "integer"
    document = GUIDE.read_text(encoding="utf-8")
    for name in ("python/quality", "python/test", "coverage_threshold"):
        assert name in document
    assert "ambiguous" in document


def test_m133_documented_output_and_workspace_edges_match_shipped_catalog() -> None:
    release = _catalog("templates/plans/oci/release-deploy/plan.yml")
    publish = _catalog("templates/goons/oci/publish-image/goon.yml")
    assert release["goons"][-1]["with"]["image"] == (
        "${{ goons.oci-publish-image.outputs.OCI_REFERENCE }}"
    )
    assert publish["goons"][-1]["with"]["source_workspace"] == (
        "${{ goons.oci-image.workspace }}"
    )
    guide = GUIDE.read_text(encoding="utf-8")
    assert "oci-publish-image.outputs.OCI_REFERENCE" in guide
    assert "oci-image.workspace" in guide
    assert "actual output value" in guide


@pytest.mark.parametrize(
    "required",
    (
        "FleetPlanResolver",
        "FleetAdmissionPlanner",
        "RUN_ADMITTED",
        "_FleetPlanRunInputs",
        "resolve_input_sources",
        "bind_inputs",
        "first defined",
        "malformed YAML/TOML",
        "staleness",
        "NOT_APPLICABLE",
        "Resolve once",
        "transport once",
        "execute once",
        "report once",
    ),
)
def test_m133_documentation_names_each_real_authority_and_failure_boundary(required: str) -> None:
    assert required in GUIDE.read_text(encoding="utf-8")
