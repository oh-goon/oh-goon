"""Shipped artifact declarations and canonical retention-guide navigation."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GOONS = ROOT / "templates/goons"
GUIDE = ROOT / "docs/development/TEMPLATE_ARTIFACT_RETENTION.md"


def _artifacts(goon: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    return tuple(
        artifact
        for step in goon.get("steps", ())
        for artifact in step.get("artifacts", ())
    )


def _errors(goon: dict[str, Any]) -> tuple[str, ...]:
    artifacts = _artifacts(goon)
    paths = [item.get("path") for item in artifacts]
    errors = []
    if len(paths) != len(set(paths)):
        errors.append("duplicate artifact path")
    for item in artifacts:
        if item.get("retention") not in ("evidence", "content_store"):
            errors.append("undefined artifact retention")
        if not isinstance(item.get("path"), str) or not item["path"].strip():
            errors.append("missing artifact path")
    return tuple(errors)


def _shipped_artifact_goons() -> tuple[tuple[Path, dict[str, Any]], ...]:
    entries = []
    for path in sorted(GOONS.glob("*/*/goon.yml")):
        goon = yaml.safe_load(path.read_text(encoding="utf-8"))
        if _artifacts(goon):
            entries.append((path, goon))
    return tuple(entries)


def test_m136_every_shipped_artifact_is_explicit_and_documented() -> None:
    entries = _shipped_artifact_goons()
    assert entries
    for path, goon in entries:
        assert _artifacts(goon), path
        assert not _errors(goon), path
        readme = path.with_name("README.md").read_text(encoding="utf-8")
        assert "TEMPLATE_ARTIFACT_RETENTION.md" in readme, path


@pytest.mark.parametrize("mutation", ["duplicate", "invalid", "missing"])
def test_m136_invalid_or_ambiguous_retention_is_detected(mutation: str) -> None:
    _, exemplar = next(
        (path, item) for path, item in _shipped_artifact_goons() if _artifacts(item)
    )
    assert not _errors(exemplar)
    changed = copy.deepcopy(exemplar)
    first = next(step for step in changed["steps"] if step.get("artifacts"))
    if mutation == "duplicate":
        first["artifacts"].append(copy.deepcopy(first["artifacts"][0]))
    elif mutation == "invalid":
        first["artifacts"][0]["retention"] = "lifetime"
    else:
        first["artifacts"][0].pop("retention")
    assert _errors(changed)


def test_retention_guide_links_cover_every_shipped_artifact_choice() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    linked_files = set()
    for link in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", text):
        if not link.startswith(("http://", "https://", "mailto:")):
            target = (GUIDE.parent / link).resolve()
            assert target.is_file(), link
            linked_files.add(target)
    for path, _ in _shipped_artifact_goons():
        assert path.resolve() in linked_files, path


def test_m136_navigation_links_to_one_canonical_guide() -> None:
    for relative in (
        "docs/README.md",
        "templates/README.md",
        "templates/goons/README.md",
        "docs/development/GOON_AUTHORING.md",
    ):
        page = ROOT / relative
        text = page.read_text(encoding="utf-8")
        assert "TEMPLATE_ARTIFACT_RETENTION.md" in text
        for match in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", text):
            if "TEMPLATE_ARTIFACT_RETENTION.md" in match:
                assert (page.parent / match).is_file(), (page, match)
