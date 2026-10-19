"""M137: catalog egress grants need one explicit semantic authority."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_plan_compilation import parse_goon_template

ROOT = Path(__file__).resolve().parents[2]
GOONS = ROOT / "templates/goons"
GUIDE = ROOT / "docs/development/TEMPLATE_NETWORK_AUTHORITY.md"
NETWORK_MODES = {"none", "cidr_allowlist", "hostname_allowlist", "host_unrestricted"}


def _shipped_goons() -> tuple[tuple[Path, dict[str, Any]], ...]:
    return tuple(
        (path, yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted(GOONS.glob("*/*/goon.yml"))
    )


def _errors(document: dict[str, Any]) -> tuple[str, ...]:
    try:
        parse_goon_template(
            yaml.safe_dump(document), source="fixture.yml", expected_name="fixture"
        )
    except FleetPlanInvalid as exc:
        return (str(exc),)
    return ()


def test_m137_shipped_network_capabilities_are_explicit_and_exceptional() -> None:
    entries = _shipped_goons()
    assert entries
    unrestricted = []
    for path, document in entries:
        assert not _errors(document), path
        for step in document.get("steps", ()):
            egress = step.get("capabilities", {}).get("egress", {})
            if egress.get("mode") == "host_unrestricted":
                unrestricted.append(path)
                readme = path.with_name("README.md").read_text(encoding="utf-8")
                assert "TEMPLATE_NETWORK_AUTHORITY.md" in readme, path
    assert unrestricted, "keep a meaningful audit as long as exceptions are shipped"


@pytest.mark.parametrize("mutation", ["unknown", "mixed"])
def test_m137_ambiguous_unrestricted_network_is_detected(mutation: str) -> None:
    _, source = next(
        (path, document)
        for path, document in _shipped_goons()
        if any(
            step.get("capabilities", {}).get("egress", {}).get("mode")
            == "host_unrestricted"
            for step in document.get("steps", ())
        )
    )
    changed = copy.deepcopy(source)
    step = changed["steps"][0]
    if mutation == "unknown":
        step["capabilities"]["egress"]["mode"] = "uncontrolled"
    else:
        step["capabilities"]["egress"]["rules"] = []
    assert _errors(changed)


def test_m137_guide_explains_both_live_scanners_and_runtime_ownership() -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    for item in (
        "host_unrestricted",
        "hostname_allowlist",
        "cidr_allowlist",
        "none",
        "FleetPlanResolver",
        "preview_plan",
        "Enforcer",
        "Run",
        "admission",
        "uv --preview audit",
        "trivy fs",
        "--no-python-downloads",
        "--skip-version-check",
    ):
        assert item in guide, item
    for path, document in _shipped_goons():
        if any(
            step.get("capabilities", {}).get("egress", {}).get("mode")
            == "host_unrestricted"
            for step in document.get("steps", ())
        ):
            identity = "/".join(path.parent.parts[-2:])
            assert f"`{identity}`" in guide, identity
    for link in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", guide):
        if not link.startswith(("https://", "http://", "mailto:")):
            assert (GUIDE.parent / link).is_file(), link


def test_m137_canonical_navigation_to_network_guide() -> None:
    for location in (
        "docs/README.md",
        "templates/README.md",
        "templates/goons/README.md",
        "docs/development/GOON_AUTHORING.md",
    ):
        page = ROOT / location
        content = page.read_text(encoding="utf-8")
        links = re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", content)
        guide_links = [
            link for link in links if "TEMPLATE_NETWORK_AUTHORITY.md" in link
        ]
        assert guide_links, location
        assert all((page.parent / link).is_file() for link in guide_links), location
