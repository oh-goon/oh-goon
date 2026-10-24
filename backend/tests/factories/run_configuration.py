"""Registered repository and explicit workflow selection for CLI tests."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from oh_goon.core.models.repository import RepositoryIdentity


def write_run_configuration(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    source: dict[str, str] | None = None,
    inputs: dict[str, Any] | None = None,
    plan_content: str | None = None,
    goon_content: str | None = None,
    agent: bool = False,
    native_agent: bool = False,
    repair: bool = False,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    repo = root / "repository"
    repo.mkdir(exist_ok=True)
    (repo / ".git").mkdir(exist_ok=True)
    preferences = root / "league.yml"
    preferences.write_text(yaml.safe_dump({"version": 1, "folders": [str(repo)]}))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(preferences))
    if plan_content is not None:
        (root / "plan.yml").write_text(plan_content, encoding="utf-8")
        source = {"kind": "file-plan", "path": "./plan.yml"}
    if goon_content is not None:
        (repo / "goon.yml").write_text(goon_content, encoding="utf-8")
        source = {"kind": "repository-goon"}
    selection: dict[str, Any] = {
        "version": 1,
        "source": source or {"kind": "catalog-plan", "name": "python/quality"},
        "target": {
            "repository_id": RepositoryIdentity.model_validate({"path": repo}).repository_id
        },
    }
    if inputs:
        selection["inputs"] = inputs
    if agent:
        selection["agent"] = {
            "model_profile": "review",
            "acceptance": "agentic-review",
            "repository_root": str(root),
            "skill_root": ".",
        }
        if native_agent:
            selection["agent"].pop("model_profile")
            selection["agent"].update(implementation="pi", profile="isolated-managed")
        if repair:
            selection["repair"] = {
                "review_passes": 2,
                "write_roots": ["src"],
                "timeout_seconds": 60,
                "step_timeout_seconds": 30,
            }
    path = root / "run.yml"
    path.write_text(yaml.safe_dump(selection), encoding="utf-8")
    return path
