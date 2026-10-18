"""M139 documentation references one real catalog owner and derived hash fields."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs/development/TEMPLATE_CATALOG_DETERMINISM.md"


def test_catalog_guide_links_and_no_shadow_index() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    assert "runtime.template_discovery.discover_template_packages" in text
    assert "FleetPlanSourceSnapshot" in text
    assert "catalog.yml" in text
    assert "sha256" in text
    for source, link in (
        (ROOT / "docs/README.md", "development/TEMPLATE_CATALOG_DETERMINISM.md"),
        (
            ROOT / "templates/README.md",
            "../docs/development/TEMPLATE_CATALOG_DETERMINISM.md",
        ),
    ):
        assert link in source.read_text(encoding="utf-8")
        assert (source.parent / link).is_file()


def test_catalog_source_owner_remains_single_discovery_function() -> None:
    discovery = (ROOT / "backend/src/oh_goon/runtime/template_discovery.py").read_text(
        encoding="utf-8"
    )
    assert "def discover_template_packages(" in discovery
    for store in ("goon_templates.py", "plan_templates.py"):
        content = (ROOT / "backend/src/oh_goon/runtime" / store).read_text(
            encoding="utf-8"
        )
        assert "return discover_template_packages(self.root," in content
        assert "sha256=source.sha256" in content
