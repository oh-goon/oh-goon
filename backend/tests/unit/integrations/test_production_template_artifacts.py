from __future__ import annotations

from pathlib import Path

import yaml

TEMPLATES = Path(__file__).resolve().parents[4] / "templates"


def test_package_artifacts_is_project_command_with_real_artifacts() -> None:
    data = yaml.safe_load(
        (TEMPLATES / "goons/python/package-artifacts/goon.yml").read_text(encoding="utf-8")
    )
    assert data["steps"] == [
        {
            "run": "uv run package-artifacts --output-dir dist --bundle package-distributions.tar --manifest package-artifacts.json",
            "artifacts": [
                {"path": "package-distributions.tar", "retention": "content_store"},
                {"path": "package-artifacts.json", "retention": "evidence"},
            ],
        }
    ]


def test_simple_command_artifacts_are_files_and_do_not_author_network_policy() -> None:
    for path in sorted((TEMPLATES / "goons").glob("*/*/goon.yml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for step in data.get("steps", ()):
            if "run" not in step:
                continue
            assert "network" not in step, path
            assert "timeout_seconds" not in step, path
            for artifact in step.get("artifacts", ()):
                assert Path(artifact["path"]).suffix, (path, artifact)
