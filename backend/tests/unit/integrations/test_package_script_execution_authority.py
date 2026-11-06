from __future__ import annotations

import json
from pathlib import Path

import pytest

from oh_goon.integrations.make_execution_authority import derive_make_target_authority
from oh_goon.integrations.package_script_execution_authority import derive_npm_run_authority
from oh_goon.integrations.shell_execution_authority import derive_shell_fragment_authority
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

    authority = derive_npm_run_authority(
        tmp_path, "verify", chain=("target", "npm run verify"), package_dir=tmp_path / "frontend"
    )

    assert authority.unresolved
    assert authority.host_tools == ("npm",)
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

    unresolved = derive_npm_run_authority(
        tmp_path, "test", chain=("target",), package_dir=tmp_path / "frontend"
    )
    assert any(
        "ambiguous package-local executable runner" in item for item in unresolved.unresolved
    )

    payload = json.loads((package / "package.json").read_text(encoding="utf-8"))
    payload["ohGoonExecutionAuthority"] = {"packageBinProviders": {"runner": "runner-a"}}
    (package / "package.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    resolved = derive_npm_run_authority(
        tmp_path, "test", chain=("target",), package_dir=tmp_path / "frontend"
    )

    assert resolved.unresolved
    assert len(resolved.package_executables) == 1
    assert resolved.package_executables[0].package_path == "node_modules/runner-a"


def test_package_bin_provider_must_be_direct_dependency(tmp_path: Path) -> None:
    _write_package(
        tmp_path,
        scripts={"test": "runner test"},
        dependencies={},
        bins={"runner-a": ("1.0.0", "runner", "cli.js")},
    )

    authority = derive_npm_run_authority(
        tmp_path, "test", chain=("target",), package_dir=tmp_path / "frontend"
    )

    assert any("provided only transitively" in item for item in authority.unresolved)


def test_dynamic_package_script_executable_fails_closed(tmp_path: Path) -> None:
    _write_package(
        tmp_path,
        scripts={"test": '"$TOOL" test'},
        dependencies={},
        bins={},
    )

    authority = derive_npm_run_authority(
        tmp_path, "test", chain=("target",), package_dir=tmp_path / "frontend"
    )

    assert authority.unresolved
    assert authority.package_executables == ()


def test_conditional_script_source_does_not_gain_effective_branch_authority(tmp_path: Path) -> None:
    package = _write_package(
        tmp_path,
        scripts={"test": "runner test"},
        dependencies={"runner": "1"},
        bins={"runner": ("1.0", "runner", "cli.js")},
    )
    baseline = derive_npm_run_authority(tmp_path, "test", chain=("target",), package_dir=package)
    assert baseline.unresolved
    assert [item.bin_name for item in baseline.package_executables] == ["runner"]
    assert baseline.repository_evidence

    manifest = package / "package.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["scripts"]["test"] = (
        'MODE=local; if [ "$MODE" = local ]; then runner test; else "$REMOTE_TOOL"; fi'
    )
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    result = derive_npm_run_authority(tmp_path, "test", chain=("target",), package_dir=package)
    assert result.unresolved
    assert any("unproven" in item for item in result.unresolved)


def test_native_npm_context_comes_from_explicit_step_cwd_not_unique_script_name(
    tmp_path: Path,
) -> None:
    package = _write_package(
        tmp_path,
        scripts={"build": "runner"},
        dependencies={"runner": "1"},
        bins={"runner": ("1", "runner", "cli.js")},
    )
    baseline = derive_npm_run_authority(tmp_path, "build", chain=("target",), package_dir=package)
    assert baseline.unresolved
    assert baseline.source_identities
    inferred = derive_npm_run_authority(tmp_path, "build", chain=("target",))
    assert inferred.unresolved
    assert [item.bin_name for item in inferred.package_executables] == ["runner"]

    argv = ("npm", "run", "build")
    explicit = derive_shell_fragment_authority(
        tmp_path, "unused", chain=("step",), argv=argv, working_directory="frontend"
    )
    assert explicit.unresolved
    assert [item.bin_name for item in explicit.package_executables] == ["runner"]
    wrong_context = derive_shell_fragment_authority(
        tmp_path, "unused", chain=("step",), argv=argv, working_directory="."
    )
    assert wrong_context.unresolved
    assert wrong_context.package_executables == ()


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

    assert authority.unresolved
    assert set(authority.tools) == {"npm"}
    assert [item.bin_name for item in authority.package_executables] == ["tsc"]
    assert {path for path, _identity in authority.source_identities} == {
        "Makefile",
        "frontend/package.json",
        "frontend/package-lock.json",
    }


def _write_forwarded_make_target(root: Path, *, payload: str = "npm run typecheck") -> None:
    _write_package(
        root,
        scripts={"typecheck": "tsc -b"},
        dependencies={"typescript": "1"},
        bins={"typescript": ("1.0.0", "tsc", "bin/tsc")},
    )
    (root / "scripts").mkdir()
    (root / "scripts/resources.sh").write_text(
        '#!/bin/bash\n# OH_GOON_FORWARD_ARGV_FROM: 1\nexec "$@"\n', encoding="utf-8"
    )
    (root / "scripts/run_step.sh").write_text(
        '#!/bin/sh\n# OH_GOON_FORWARD_ARGV_FROM: 4\nshift 3\n"$@"\n', encoding="utf-8"
    )
    (root / "Makefile").write_text(
        "BASH = bash\nSH = sh\n"
        "RESOURCE_SCRIPT = scripts/resources.sh\nSTEP_SCRIPT = scripts/run_step.sh\n"
        "RESOURCES = $(BASH) $(RESOURCE_SCRIPT)\nRUN_STEP = $(SH) $(STEP_SCRIPT)\n"
        f"COMMAND = {payload}\nPAYLOAD = $(COMMAND)\n"
        "target:\n\t@$(RESOURCES) $(RUN_STEP) 'TYPE CHECK' 'Owner: frontend' -- "
        "sh -c 'cd frontend && exec \"$$@\"' sh $(PAYLOAD)\n",
        encoding="utf-8",
    )


def test_nested_make_forwarders_capture_scripts_native_tools_and_locked_bins(
    tmp_path: Path,
) -> None:
    _write_forwarded_make_target(tmp_path)
    authority = derive_make_target_authority(tmp_path, "target")

    assert authority.unresolved
    assert set(authority.tools) == {"bash", "sh", "npm"}
    assert [item.bin_name for item in authority.package_executables] == ["tsc"]
    identities = dict(authority.source_identities)
    assert set(identities) == {
        "Makefile",
        "scripts/resources.sh",
        "scripts/run_step.sh",
        "frontend/package.json",
        "frontend/package-lock.json",
    }
    for relative in ("scripts/resources.sh", "scripts/run_step.sh"):
        script = tmp_path / relative
        original = script.read_text(encoding="utf-8")
        script.write_text(
            original.replace("\n", "\nprintf 'changed output\\n'\n", 1), encoding="utf-8"
        )
        changed = derive_make_target_authority(tmp_path, "target")
        assert dict(changed.source_identities)[relative] != identities[relative]
        script.write_text(original, encoding="utf-8")


def test_nested_make_forwarded_payload_mutation_exposes_the_new_tool(tmp_path: Path) -> None:
    _write_forwarded_make_target(tmp_path)
    before = derive_make_target_authority(tmp_path, "target")
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        makefile.read_text(encoding="utf-8").replace(
            "COMMAND = npm run typecheck", "COMMAND = rm -f artifact"
        ),
        encoding="utf-8",
    )
    after = derive_make_target_authority(tmp_path, "target")

    assert before.unresolved and after.unresolved
    assert "npm" in before.tools and "rm" not in before.tools
    assert "rm" in after.tools and "npm" not in after.tools
    assert after.package_executables == ()
    assert dict(before.source_identities)["Makefile"] != dict(after.source_identities)["Makefile"]


@pytest.mark.parametrize(
    "payload", ["$(COMMAND)", "$(UNBOUND)", "$(shell printf npm) run typecheck"]
)
def test_unproved_forwarded_make_values_cannot_gain_ambient_execution_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: str
) -> None:
    _write_forwarded_make_target(tmp_path, payload=payload)
    monkeypatch.setenv("UNBOUND", "rm -f artifact")

    authority = derive_make_target_authority(tmp_path, "target")

    assert authority.unresolved
    assert "rm" not in authority.tools
    assert authority.package_executables == ()


@pytest.mark.parametrize(
    "invocation",
    [
        "$(MAKE) child",
        "$(RUN)",
        "$(NESTED)",
        "${MAKE} child",
        "$(MAKE) --directory=elsewhere child",
        "$(MAKE) child -fOtherfile",
        "$(MAKE) child COMMAND=curl",
        "$(MAKE) child SHELL=curl",
        "$(MAKE) child MAKEFLAGS=-fOtherfile",
        "$(MAKE) $(UNBOUND)",
    ],
)
def test_recursive_make_native_binding_remains_unproven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invocation: str
) -> None:
    makefile = tmp_path / "Makefile"
    baseline_source = "target: child\nchild:\n\tgit --version\n"
    makefile.write_text(baseline_source, encoding="utf-8")
    baseline = derive_make_target_authority(tmp_path, "target")
    assert baseline.unresolved == ()
    assert baseline.tools == ("git",)

    monkeypatch.setenv("UNBOUND", "child")
    monkeypatch.setenv("MAKE", "make")
    makefile.write_text(
        f"RUN = $(MAKE) child\nNESTED = $(RUN)\ntarget:\n\t{invocation}\nchild:\n\tgit --version\n",
        encoding="utf-8",
    )
    result = derive_make_target_authority(tmp_path, "target")
    assert result.unresolved
    assert "curl" not in result.tools
    assert "Makefile" in dict(result.source_identities)


def test_declared_forward_need_does_not_widen_independent_tool_permission(tmp_path: Path) -> None:
    from oh_goon.services.canonical_goon_tool_projection import (
        derive_canonical_goon_tool_projection,
    )

    _write_forwarded_make_target(tmp_path, payload="rm -f artifact")
    backend = tmp_path / "backend"
    backend.mkdir(exist_ok=True)
    (backend / "goon-execution-requirements.json").write_text(
        json.dumps({"workload_tool_permissions": {"make:target": ["bash", "sh"]}}),
        encoding="utf-8",
    )
    goon = tmp_path / "templates/goons/oh-goon/candidate"
    goon.mkdir(parents=True)
    (goon / "goon.yml").write_text("steps:\n- run: make target\n", encoding="utf-8")
    (goon / "README.md").write_text("# oh-goon/candidate\n", encoding="utf-8")
    workloads = load_workload_execution_requirements(tmp_path)
    assert workloads.unresolved_by_owner["make:target"]
    assert "rm" in workloads.derived_tools_by_owner["make:target"]

    projection = derive_canonical_goon_tool_projection(
        tmp_path,
        workload_permissions={"make:target": ("bash", "sh")},
        workloads=workloads,
    )
    assert len(projection.permission_violations) == 1
    assert projection.permission_violations[0].missing_tools == ("rm",)


def test_canonical_frontend_authority_closes_locked_package_executables() -> None:
    root = Path(__file__).resolve().parents[4]
    requirements = load_workload_execution_requirements(root)

    functional = requirements.package_executables_by_owner["make:frontend-functional-certification"]
    assert [item.bin_name for item in functional] == ["depcruise", "knip", "tsc", "vite", "vitest"]
    browser = requirements.package_executables_by_owner["make:browser-harness-preflight"]
    assert [(item.bin_name, item.package_path) for item in browser] == [
        ("playwright", "node_modules/@playwright/test")
    ]


@pytest.mark.parametrize(
    "body",
    [
        "printf '%s' ${UNSET:-`curl --version`} && tsc -b",
        "printf '%s' \"${UNSET:-`curl --version`}\" && tsc -b",
        "printf '%s' \"${UNSET:-'`curl --version`'}\" && tsc -b",
        "printf '%s' \"$((`getconf LONG_BIT`))\" && tsc -b",
        "printf '%s' \"$(( '`getconf LONG_BIT`' ))\" && tsc -b",
        "printf '%s' \"${UNSET:-$(( '$(getconf LONG_BIT)' ))}\" && tsc -b",
        "printf '%s' ${UNSET:-<(curl --version)} && tsc -b",
        "printf '%s' $(printf '%s' ${UNSET:-`curl --version`}) && tsc -b",
    ],
)
def test_npm_nested_expansions_cannot_hide_unowned_native_execution(
    tmp_path: Path, body: str
) -> None:
    package = _write_package(
        tmp_path,
        scripts={
            "test": "printf '%s' literal-data && tsc -b",
        },
        dependencies={"typescript": "1"},
        bins={"typescript": ("1.0.0", "tsc", "bin/tsc")},
    )
    baseline = derive_npm_run_authority(
        tmp_path, "test", chain=("target",), package_dir=tmp_path / "frontend"
    )
    assert baseline.unresolved
    assert baseline.host_tools == ()
    assert [item.bin_name for item in baseline.package_executables] == ["tsc"]

    path = package / "package.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["scripts"]["test"] = body
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    authority = derive_npm_run_authority(
        tmp_path, "test", chain=("target",), package_dir=tmp_path / "frontend"
    )

    if "getconf" in body:
        assert authority.unresolved or "getconf" in authority.host_tools
    else:
        assert authority.unresolved or "curl" in authority.host_tools
