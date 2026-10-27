from __future__ import annotations

import json
from pathlib import Path

import pytest

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

    assert authority.unresolved == ()
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

    assert before.unresolved == after.unresolved == ()
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


@pytest.mark.parametrize("invocation", ["$(MAKE) child", "$(RUN)", "$(NESTED)"])
def test_recursive_make_aliases_retain_child_tools_and_source_identity(
    tmp_path: Path, invocation: str
) -> None:
    makefile = tmp_path / "Makefile"
    source = (
        f"RUN = $(MAKE) child\nNESTED = $(RUN)\ntarget:\n\t@{invocation}\nchild:\n\tgit --version\n"
    )
    makefile.write_text(source, encoding="utf-8")

    baseline = derive_make_target_authority(tmp_path, "target")

    assert baseline.unresolved == ()
    assert baseline.tools == ("git",)
    assert any("target -> child" in item for _tool, item in baseline.provenance)

    makefile.write_text(source.replace("git --version", "curl --version"), encoding="utf-8")
    changed = derive_make_target_authority(tmp_path, "target")

    assert changed.unresolved == ()
    assert changed.tools == ("curl",)
    assert (
        dict(changed.source_identities)["Makefile"] != dict(baseline.source_identities)["Makefile"]
    )


@pytest.mark.parametrize("invocation", ["$(MAKE) child", "$(RUN)", "$(NESTED)"])
def test_recursive_make_aliases_reject_missing_children(tmp_path: Path, invocation: str) -> None:
    makefile = tmp_path / "Makefile"
    source = (
        f"RUN = $(MAKE) child\nNESTED = $(RUN)\ntarget:\n\t@{invocation}\nchild:\n\tgit --version\n"
    )
    makefile.write_text(source, encoding="utf-8")
    baseline = derive_make_target_authority(tmp_path, "target")
    assert baseline.unresolved == ()
    assert baseline.tools == ("git",)

    makefile.write_text(source.replace("\nchild:\n", "\nother:\n"), encoding="utf-8")
    authority = derive_make_target_authority(tmp_path, "target")

    assert any(
        "missing recursive make target: target -> child" in item for item in authority.unresolved
    )
    assert "git" not in authority.tools


@pytest.mark.parametrize("invocation", ["$(MAKE) child", "$(RUN)", "$(NESTED)"])
def test_recursive_make_aliases_reject_child_cycles(tmp_path: Path, invocation: str) -> None:
    makefile = tmp_path / "Makefile"
    source = (
        "RUN = $(MAKE) child\nNESTED = $(RUN)\nLOOP = $(MAKE) target\n"
        f"target:\n\t@{invocation}\nchild:\n\tgit --version\n"
    )
    makefile.write_text(source, encoding="utf-8")
    baseline = derive_make_target_authority(tmp_path, "target")
    assert baseline.unresolved == ()
    assert baseline.tools == ("git",)

    makefile.write_text(source.replace("git --version", "$(LOOP)"), encoding="utf-8")
    authority = derive_make_target_authority(tmp_path, "target")

    assert any(
        "make target cycle: target -> child -> target" in item for item in authority.unresolved
    )


@pytest.mark.parametrize(
    "invocation",
    [
        "$(MAKE)",
        "$(MAKE) $(UNBOUND)",
        "$(MAKE) --directory=elsewhere child",
        "$(MAKE) child --directory=elsewhere",
        "$(MAKE) child -C elsewhere",
        "$(MAKE) child -fOtherfile",
        "$(MAKE) child $(UNBOUND)",
        '$(MAKE) child "TAIL=$(UNBOUND)"',
        "$(MAKE) child SHELL=curl",
        "$(MAKE) child MAKEFLAGS=-fOtherfile",
        "$(MAKE) child MAKE=other-make",
        "$(MAKE) child MFLAGS=-Celsewhere",
        "$(MAKE) child MAKEFILES=Otherfile",
        "${MAKE} child",
    ],
)
def test_unsupported_recursive_make_aliases_remain_unproven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invocation: str
) -> None:
    makefile = tmp_path / "Makefile"
    source = (
        "RUN = $(MAKE) child\nNESTED = $(RUN)\ntarget:\n\t@$(NESTED)\nchild:\n\tgit --version\n"
        "-C:\n\tcurl --version\n-fOtherfile:\n\tcurl --version\n"
    )
    makefile.write_text(source, encoding="utf-8")
    baseline = derive_make_target_authority(tmp_path, "target")
    assert baseline.unresolved == ()
    assert baseline.tools == ("git",)

    monkeypatch.setenv("UNBOUND", "child")
    monkeypatch.setenv("MAKE", "make")
    makefile.write_text(
        source.replace("RUN = $(MAKE) child", f"RUN = {invocation}"), encoding="utf-8"
    )
    authority = derive_make_target_authority(tmp_path, "target")

    assert authority.unresolved
    assert "git" not in authority.tools


@pytest.mark.parametrize("invocation", ["$(MAKE) child", "$(RUN)", "$(NESTED)"])
def test_recursive_make_aliases_retain_every_literal_goal(tmp_path: Path, invocation: str) -> None:
    makefile = tmp_path / "Makefile"
    source = (
        "RUN = $(MAKE) child\nNESTED = $(RUN)\n"
        f"target:\n\t@{invocation}\nchild:\n\tgit --version\nother:\n\tcurl --version\n"
    )
    makefile.write_text(source, encoding="utf-8")
    baseline = derive_make_target_authority(tmp_path, "target")
    assert baseline.unresolved == ()
    assert baseline.tools == ("git",)

    makefile.write_text(source.replace("$(MAKE) child", "$(MAKE) child other"), encoding="utf-8")
    authority = derive_make_target_authority(tmp_path, "target")

    assert authority.unresolved == ()
    assert authority.tools == ("curl", "git")
    assert any("target -> other" in item for _tool, item in authority.provenance)

    makefile.write_text(source.replace("$(MAKE) child", "$(MAKE) child missing"), encoding="utf-8")
    missing = derive_make_target_authority(tmp_path, "target")
    assert any(
        "missing recursive make target: target -> missing" in item for item in missing.unresolved
    )


def test_recursive_make_literal_assignments_bind_each_child_without_leaking(
    tmp_path: Path,
) -> None:
    makefile = tmp_path / "Makefile"
    source = (
        "COMMAND = git\nRUN = $(MAKE) child\nNESTED = $(RUN)\n"
        "target:\n\t@$(NESTED)\nchild:\n\t$(MAKE) grandchild\n"
        "grandchild:\n\t$(COMMAND) --version\n"
    )
    makefile.write_text(source, encoding="utf-8")
    baseline = derive_make_target_authority(tmp_path, "target")
    assert baseline.unresolved == ()
    assert baseline.tools == ("git",)

    makefile.write_text(
        source.replace(
            "\t@$(NESTED)\n",
            "\t@$(NESTED) COMMAND=curl\n\t@$(NESTED) COMMAND=rm\n\t@$(NESTED)\n",
        ),
        encoding="utf-8",
    )
    authority = derive_make_target_authority(tmp_path, "target")

    assert authority.unresolved == ()
    assert authority.tools == ("curl", "git", "rm")
    assert all("target -> child -> grandchild" in item for _tool, item in authority.provenance)


def test_recursive_make_retains_literal_marker_assignment_data(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    source = (
        "MARKER = not certification\nRUN = $(MAKE) child\nNESTED = $(RUN)\n"
        'target:\n\t@$(NESTED)\nchild:\n\tgit log --grep "$(MARKER)"\n'
    )
    makefile.write_text(source, encoding="utf-8")
    baseline = derive_make_target_authority(tmp_path, "target")
    assert baseline.unresolved == ()
    assert baseline.tools == ("git",)

    makefile.write_text(
        source.replace("\t@$(NESTED)\n", '\t@$(NESTED) MARKER="not certification and not slow"\n'),
        encoding="utf-8",
    )
    authority = derive_make_target_authority(tmp_path, "target")

    assert authority.unresolved == ()
    assert authority.tools == baseline.tools


def test_recursive_make_literal_assignment_keeps_cycle_detection(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    source = (
        "COMMAND = git\nRUN = $(MAKE) child\nNESTED = $(RUN)\n"
        "target:\n\t@$(NESTED) COMMAND=curl\nchild:\n\t$(COMMAND) --version\n"
    )
    makefile.write_text(source, encoding="utf-8")
    baseline = derive_make_target_authority(tmp_path, "target")
    assert baseline.unresolved == ()
    assert baseline.tools == ("curl",)

    makefile.write_text(
        source.replace("$(COMMAND) --version", "$(MAKE) target COMMAND=git"), encoding="utf-8"
    )
    authority = derive_make_target_authority(tmp_path, "target")

    assert any(
        "make target cycle: target -> child -> target" in item for item in authority.unresolved
    )


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
            "test": "printf '%s' '${UNSET:-`curl --version`}' "
            r'"\${UNSET:-\`curl --version\`}" '
            "${UNSET:-'`curl --version`'} "
            r"${UNSET:-\<\(curl\)} "
            '"${UNSET:-$(getconf LONG_BIT)}" && tsc -b',
        },
        dependencies={"typescript": "1"},
        bins={"typescript": ("1.0.0", "tsc", "bin/tsc")},
    )
    baseline = derive_npm_run_authority(tmp_path, "test", chain=("target",))
    assert baseline.unresolved == ()
    assert baseline.host_tools == ("getconf", "sh")
    assert [item.bin_name for item in baseline.package_executables] == ["tsc"]

    path = package / "package.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["scripts"]["test"] = body
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    authority = derive_npm_run_authority(tmp_path, "test", chain=("target",))

    if "getconf" in body:
        assert authority.unresolved or "getconf" in authority.host_tools
    else:
        assert authority.unresolved or "curl" in authority.host_tools
