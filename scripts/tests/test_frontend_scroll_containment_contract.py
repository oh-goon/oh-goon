from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _rule(css: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\{{([^}}]+)\}}", css)
    assert match is not None, f"missing CSS rule for {selector}"
    return match.group(1)


def test_wide_table_scroll_owners_bound_their_inline_size_generically() -> None:
    layout = (ROOT / "frontend/src/styles/pageLayout.css").read_text(encoding="utf-8")
    responsive = (ROOT / "frontend/src/styles/responsive.css").read_text(encoding="utf-8")

    for selector in (".table-shell", ".execution-timeline__scroll"):
        body = _rule(layout, selector)
        assert "width:100%" in body
        assert "min-width:0" in body
        assert "max-width:100%" in body
        assert "contain:inline-size" in body
        assert "overflow-x:auto" in body or "overflow:auto" in body

    # The containment contract must not exist only inside the narrow mobile breakpoint;
    # tablet and 200%-reflow browser gates exercise the same wide-table ownership.
    responsive_body = _rule(responsive, ".table-shell")
    assert "contain:inline-size" not in responsive_body
    assert "overflow-x:auto" not in responsive_body


def test_mobile_fixed_navigation_uses_insets_instead_of_base_full_width() -> None:
    responsive = (ROOT / "frontend/src/styles/responsive.css").read_text(encoding="utf-8")
    match = re.search(r"\.app-nav\{position:fixed;([^}]*)\}", responsive)
    assert match is not None, "missing fixed mobile navigation rule"
    body = match.group(1)
    assert "left:8px" in body
    assert "right:8px" in body
    assert "width:auto" in body
    assert "overflow-x:auto" in body
