"""M138: protect the *meaning* of shared commands and separate browser preflights."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GOONS = ROOT / "templates/goons/oh-goon"
GUIDE = ROOT / "docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md"
BROWSER_TARGETS = {
    "browser-localhost-transport-preflight": "browser-localhost-transport-preflight",
    "browser-engine-preflight": "browser-preflight",
    "browser-harness-preflight": "browser-harness-preflight",
    "browser-runtime-preflight": "browser-runtime-preflight",
}


def _goon(key: str) -> dict:
    return yaml.safe_load((GOONS / key / "goon.yml").read_text(encoding="utf-8"))


def _workspace_errors(repository: dict, producer: dict) -> tuple[str, ...]:
    """Test-only convergence check, not another parser or runtime decision maker."""
    problems: list[str] = []
    if repository["steps"][0]["run"] != producer["steps"][0]["run"]:
        problems.append("integration pair no longer shares its native suite")
    if repository.get("inputs", {}).get("source_workspace") or repository["steps"][
        0
    ].get("workspace"):
        problems.append("repository integration Goon acquired producer authority")
    source = producer.get("inputs", {}).get("source_workspace", {})
    if (
        source.get("type") != "workspace_reference"
        or "default" in source
        or source.get("optional")
    ):
        problems.append("producer workspace must be a required typed input")
    workspace = producer["steps"][0].get("workspace")
    if workspace != {"from": "step", "step": "${{ inputs.source_workspace }}"}:
        problems.append("integration contract lost exact producer binding")
    return tuple(problems)


def _browser_errors(goons: dict[str, dict]) -> tuple[str, ...]:
    problems = []
    seen_commands: set[str] = set()
    for key, native_target in BROWSER_TARGETS.items():
        candidate = goons[key]
        if (
            candidate.get("inputs", {}).get("source_workspace", {}).get("type")
            != "workspace_reference"
        ):
            problems.append(f"{key}: missing workspace input")
        if candidate["steps"][0].get("workspace") != {
            "from": "step",
            "step": "${{ inputs.source_workspace }}",
        }:
            problems.append(f"{key}: missing Step source binding")
        command = candidate["steps"][0].get("run")
        if command != f"make {native_target}" or command in seen_commands:
            problems.append(f"{key}: collapsed or changed native command")
        seen_commands.add(command)
    return tuple(problems)


def test_m138_same_native_integration_suite_has_two_distinct_workspace_contracts() -> (
    None
):
    assert not _workspace_errors(
        _goon("integration-tests"), _goon("integration-contract")
    )


@pytest.mark.parametrize(
    "mutation",
    ["missing-input", "repository-workspace", "wrong-producer", "implicit-repository"],
)
def test_m138_rejects_integration_semantic_collapse(mutation: str) -> None:
    repository = _goon("integration-tests")
    producer = copy.deepcopy(_goon("integration-contract"))
    if mutation == "missing-input":
        producer.pop("inputs")
    elif mutation == "repository-workspace":
        producer["steps"][0]["workspace"] = {"from": "repository"}
    elif mutation == "wrong-producer":
        producer["steps"][0]["workspace"]["step"] = "unbound"
    else:
        repository["steps"][0]["workspace"] = {"from": "step", "step": "someone"}
    assert _workspace_errors(repository, producer)


def test_m138_browser_preflights_keep_four_independent_boundaries() -> None:
    assert not _browser_errors({key: _goon(key) for key in BROWSER_TARGETS})
    scripts = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))[
        "scripts"
    ]
    for native_script in (
        "browser:preflight",
        "browser:harness-preflight",
        "browser:runtime-preflight",
    ):
        assert scripts[native_script].startswith(
            "npm run browser:transport-preflight && "
        )
    assert scripts["browser:transport-preflight"].startswith(
        "node e2e/browser-localhost-transport-preflight.mjs"
    )
    make = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in BROWSER_TARGETS.values():
        assert re.search(rf"^{re.escape(target)}:\s*$", make, re.MULTILINE), target
    for certification_target in (
        "browser-targeted-certification",
        "browser-required-certification",
    ):
        assert re.search(
            rf"^{certification_target}: browser-runtime-preflight$", make, re.MULTILINE
        )


@pytest.mark.parametrize(
    "mutation", ["merged-command", "missing-workspace", "changed-input"]
)
def test_m138_browser_semantic_collapse_is_detected(mutation: str) -> None:
    goons = {key: _goon(key) for key in BROWSER_TARGETS}
    if mutation == "merged-command":
        goons["browser-runtime-preflight"]["steps"][0]["run"] = goons[
            "browser-harness-preflight"
        ]["steps"][0]["run"]
    elif mutation == "missing-workspace":
        goons["browser-engine-preflight"]["steps"][0].pop("workspace")
    else:
        goons["browser-harness-preflight"]["inputs"]["source_workspace"]["type"] = (
            "string"
        )
    assert _browser_errors(goons)


def test_m138_canonical_guide_and_package_readmes_converge() -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    for key in ("integration-tests", "integration-contract", *BROWSER_TARGETS):
        assert f"`oh-goon/{key}`" in guide or f"`{key}`" in guide, key
        readme = (GOONS / key / "README.md").read_text(encoding="utf-8")
        assert "TEMPLATE_SELF_HOSTING_BOUNDARIES.md" in readme, key
    for phrase in (
        "source_workspace",
        "producer",
        "repository",
        "transport",
        "engine",
        "harness",
        "runtime",
        "Game Tape",
    ):
        assert phrase in guide, phrase
    for source in (
        GUIDE,
        ROOT / "docs/README.md",
        ROOT / "templates/README.md",
        ROOT / "templates/goons/README.md",
        ROOT / "docs/development/GOON_AUTHORING.md",
    ):
        links = re.findall(
            r"\]\(([^)#]+)(?:#[^)]*)?\)", source.read_text(encoding="utf-8")
        )
        for link in links:
            if not link.startswith(("http://", "https://", "mailto:")):
                assert (source.parent / link).is_file(), (source, link)
