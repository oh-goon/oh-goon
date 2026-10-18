from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]
GOON = ROOT / "templates/goons/python/release-metadata/goon.yml"


def _data() -> dict:
    return yaml.safe_load(GOON.read_text(encoding="utf-8"))


def test_release_metadata_uses_project_command_not_embedded_program() -> None:
    data = _data()
    assert data.get("requirements") is None
    assert data["uv"] == {"group": "dev"}
    assert data["steps"] == [
        {
            "id": "compute",
            "run": "uv run release-metadata --channel ${{ inputs.release_channel }} --revision ${{ inputs.revision }} --output-dir .fleet/release",
            "outputs": {
                "PROJECT_VERSION": ".fleet/release/PROJECT_VERSION",
                "RELEASE_VERSION": ".fleet/release/RELEASE_VERSION",
            },
        }
    ]


def test_release_metadata_owns_only_its_input_contract_and_outputs() -> None:
    data = _data()
    assert data["inputs"]["release_channel"] == {
        "type": "choice",
        "choices": ["alpha", "beta", "production"],
        "default": "alpha",
    }
    assert data["inputs"]["revision"] == {"type": "git_revision"}
    assert data["outputs"] == {
        "project_version": "${{ steps.compute.outputs.PROJECT_VERSION }}",
        "release_version": "${{ steps.compute.outputs.RELEASE_VERSION }}",
    }
