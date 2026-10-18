"""M141: inspection documentation must remain navigable and truthful."""

from pathlib import Path
import re

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
