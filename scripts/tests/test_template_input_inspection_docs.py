"""M141: inspection documentation must remain navigable and truthful."""

from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs/development/TEMPLATE_INPUT_INSPECTION.md"
LINKED = (
    ROOT / "docs/README.md",
    ROOT / "templates/README.md",
    ROOT / "docs/development/TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md",
)


def _broken_links(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [
        link
        for link in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", text)
        if not link.startswith(("https://", "http://", "mailto:"))
        and not (path.parent / link).resolve().is_file()
    ]


def test_inspection_navigation_and_links_are_current():
    for path in (GUIDE, *LINKED):
        assert not _broken_links(path), path
        if path != GUIDE:
            assert "TEMPLATE_INPUT_INSPECTION.md" in path.read_text(encoding="utf-8")


def test_shipped_leaf_owns_documented_contract_not_plan():
    plan = yaml.safe_load(
        (ROOT / "templates/plans/python/quality/plan.yml").read_text()
    )
    composite = yaml.safe_load(
        (ROOT / "templates/goons/python/quality/goon.yml").read_text()
    )
    leaf = yaml.safe_load((ROOT / "templates/goons/python/test/goon.yml").read_text())
    assert plan["inputs"] == ["coverage_threshold"]
    assert composite["goons"][-1]["pass"] == ["coverage_threshold"]
    assert "coverage_threshold" not in composite["goons"][-1].get("with", {})
    assert leaf["inputs"]["coverage_threshold"] == {
        "type": "integer",
        "min": 70,
        "max": 100,
        "default": 70,
    }
    guide = GUIDE.read_text(encoding="utf-8")
    for phrase in (
        "FleetPlanResolver.inspect_inputs",
        "owner_paths",
        "PlanInputDefinition",
        "python/quality",
        "python/test",
        "coverage_threshold",
        "read-only",
        "no `RUN_ADMITTED`",
        "Resolve once",
    ):
        assert phrase in guide
