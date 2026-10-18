"""M136: shipped template retention choices must match the runtime contract."""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GOONS = ROOT / "templates/goons"
GUIDE = ROOT / "docs/development/TEMPLATE_ARTIFACT_RETENTION.md"


def _artifacts(goon: dict) -> tuple[dict, ...]:
    return tuple(
        artifact
        for step in goon.get("steps", ())
        for artifact in step.get("artifacts", ())
    )


def _errors(goon: dict) -> tuple[str, ...]:
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


def _shipped_artifact_goons() -> tuple[tuple[Path, dict], ...]:
    return tuple(
        (path, yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted(GOONS.glob("*/*/goon.yml"))
        if "artifacts:" in path.read_text(encoding="utf-8")
    )


def test_m136_every_shipped_artifact_is_explicit_and_documented() -> None:
    entries = _shipped_artifact_goons()
    assert entries
    for path, goon in entries:
        assert _artifacts(goon), path
        assert not _errors(goon), path
        readme = path.with_name("README.md").read_text(encoding="utf-8")
        assert "TEMPLATE_ARTIFACT_RETENTION.md" in readme, path
        for choice in set(item["retention"] for item in _artifacts(goon)):
            assert f"`{choice}`" in readme, (path, choice)


@pytest.mark.parametrize("mutation", ["duplicate", "invalid", "missing"])
def test_m136_invalid_or_ambiguous_retention_is_detected(mutation: str) -> None:
    _, exemplar = next((path, item) for path, item in _shipped_artifact_goons() if _artifacts(item))
    changed = copy.deepcopy(exemplar)
    first = next(step for step in changed["steps"] if step.get("artifacts"))
    if mutation == "duplicate":
        first["artifacts"].append(copy.deepcopy(first["artifacts"][0]))
    elif mutation == "invalid":
        first["artifacts"][0]["retention"] = "lifetime"
    else:
        first["artifacts"][0].pop("retention")
    assert _errors(changed)


def test_m136_guide_covers_runtime_owners_and_every_shipped_choice() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    for contract in (
        "PlanArtifactDeclaration", "PresentPlanArtifact", "MissingPlanArtifact",
        "PlanRetainedContentEvidence", "fleet_plan_step_publication.py",
        "fleet_content_maintenance.py", "fleet_content_retention.py",
        "repository-scoped", "strict execution", "Run ID", "Plan ID",
    ):
        assert contract in text
    for path, _ in _shipped_artifact_goons():
        identity = "/".join(path.parent.parts[-2:])
        assert f"`{identity}`" in text, identity
    for link in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", text):
        if not link.startswith(("http://", "https://", "mailto:")):
            assert (GUIDE.parent / link).is_file(), link


def test_m136_navigation_links_to_one_canonical_guide() -> None:
    for relative in (
        "docs/README.md", "templates/README.md",
        "templates/goons/README.md", "docs/development/GOON_AUTHORING.md",
    ):
        page = ROOT / relative
        text = page.read_text(encoding="utf-8")
        assert "TEMPLATE_ARTIFACT_RETENTION.md" in text
        for match in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", text):
            if "TEMPLATE_ARTIFACT_RETENTION.md" in match:
                assert (page.parent / match).is_file(), (page, match)
