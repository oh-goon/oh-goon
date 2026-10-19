from __future__ import annotations

import json
from pathlib import Path

from oh_goon.integrations.make_execution_authority import derive_make_target_authority
from oh_goon.integrations.package_script_execution_authority import derive_npm_run_authority
from oh_goon.integrations.workload_execution_requirements import (
    load_workload_execution_requirements,
)


def _write_package(
    root: Path,
    *,
    scripts: dict[str, str],
    dependencies: dict[str, str],
    bins: dict[str, tuple[str, str, str]],
    providers: dict[str, str] | None = None,
) -> Path:
    package = root / "frontend"
    package.mkdir(parents=True)
    payload: dict[str, object] = {
        "name": "fixture",
        "private": True,
        "scripts": scripts,
        "devDependencies": dependencies,
    }
    if providers is not None:
        payload["ohGoonExecutionAuthority"] = {"packageBinProviders": providers}
    (package / "package.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    packages: dict[str, object] = {"": {"name": "fixture", "devDependencies": dependencies}}
    for package_name, (version, bin_name, target) in bins.items():
        packages[f"node_modules/{package_name}"] = {
            "version": version,
            "integrity": f"sha512-{package_name}",
            "bin": {bin_name: target},
        }
    (package / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": packages}, indent=2) + "\n",
        encoding="utf-8",
    )
    return package


def test_npm_script_closure_recurses_and_records_locked_package_bins(tmp_path: Path) -> None:
    _write_package(
        tmp_path,
        scripts={
            "verify": "npm run typecheck && npm run build",
            "typecheck": "tsc -b",
            "build": "vite build",
        },
        dependencies={"typescript": "1", "vite": "1"},
        bins={
            "typescript": ("1.0.0", "tsc", "bin/tsc"),
            "vite": ("1.0.0", "vite", "bin/vite.js"),
        },
    )

    authority = derive_npm_run_authority(tmp_path, "verify", chain=("target", "npm run verify"))

    assert authority.unresolved == ()
    assert authority.host_tools == ("npm", "sh")
    assert [item.bin_name for item in authority.package_executables] == ["tsc", "vite"]
    assert authority.scripts == ("build", "typecheck", "verify")
    assert authority.sources == ("frontend/package.json", "frontend/package-lock.json")


def test_ambiguous_package_bin_requires_explicit_direct_provider(tmp_path: Path) -> None:
    package = _write_package(
        tmp_path,
        scripts={"test": "runner test"},
        dependencies={"runner-a": "1"},
        bins={
            "runner-a": ("1.0.0", "runner", "cli.js"),
            "runner-b": ("1.0.0", "runner", "cli.js"),
        },
    )

    unresolved = derive_npm_run_authority(tmp_path, "test", chain=("target",))
    assert any(
        "ambiguous package-local executable runner" in item for item in unresolved.unresolved
    )

    payload = json.loads((package / "package.json").read_text(encoding="utf-8"))
    payload["ohGoonExecutionAuthority"] = {"packageBinProviders": {"runner": "runner-a"}}
    (package / "package.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    resolved = derive_npm_run_authority(tmp_path, "test", chain=("target",))

    assert resolved.unresolved == ()
    assert len(resolved.package_executables) == 1
    assert resolved.package_executables[0].package_path == "node_modules/runner-a"


def test_package_bin_provider_must_be_direct_dependency(tmp_path: Path) -> None:
    _write_package(
        tmp_path,
        scripts={"test": "runner test"},
        dependencies={},
        bins={"runner-a": ("1.0.0", "runner", "cli.js")},
    )

    authority = derive_npm_run_authority(tmp_path, "test", chain=("target",))

    assert any("provided only transitively by runner-a" in item for item in authority.unresolved)


def test_dynamic_package_script_executable_fails_closed(tmp_path: Path) -> None:
    _write_package(
        tmp_path,
        scripts={"test": '"$TOOL" test'},
        dependencies={},
        bins={},
    )

    authority = derive_npm_run_authority(tmp_path, "test", chain=("target",))

    assert any(
        "unresolved package-script executable variable" in item for item in authority.unresolved
    )


def test_make_authority_binds_package_sources_and_package_executables(tmp_path: Path) -> None:
    _write_package(
        tmp_path,
        scripts={"typecheck": "tsc -b"},
        dependencies={"typescript": "1"},
        bins={"typescript": ("1.0.0", "tsc", "bin/tsc")},
    )
    (tmp_path / "Makefile").write_text(
        "target:\n\tcd frontend && npm run typecheck\n",
        encoding="utf-8",
    )

    authority = derive_make_target_authority(tmp_path, "target")

    assert authority.unresolved == ()
    assert set(authority.tools) == {"npm", "sh"}
    assert [item.bin_name for item in authority.package_executables] == ["tsc"]
    assert {path for path, _identity in authority.source_identities} == {
        "Makefile",
        "frontend/package.json",
        "frontend/package-lock.json",
    }


def test_canonical_frontend_authority_closes_locked_package_executables() -> None:
    root = Path(__file__).resolve().parents[4]
    requirements = load_workload_execution_requirements(root)

    functional = requirements.package_executables_by_owner["make:frontend-functional-certification"]
    assert [item.bin_name for item in functional] == ["depcruise", "knip", "tsc", "vite", "vitest"]
    browser = requirements.package_executables_by_owner["make:browser-harness-preflight"]
    assert [(item.bin_name, item.package_path) for item in browser] == [
        ("playwright", "node_modules/@playwright/test")
    ]
