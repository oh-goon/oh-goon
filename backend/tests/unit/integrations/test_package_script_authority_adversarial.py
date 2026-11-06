from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from oh_goon.integrations import package_script_execution_authority as subject
from oh_goon.integrations.hashmarks import hashmarks_source_scope


def _package(root: Path, name: str, *, scripts: object, lock_packages: object) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "package.json").write_text(
        json.dumps({"name": name, "scripts": scripts}) + "\n", encoding="utf-8"
    )
    (directory / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": lock_packages}) + "\n", encoding="utf-8"
    )
    return directory


def test_package_discovery_uses_admitted_source_and_refuses_unsupported_manifests(
    tmp_path: Path,
) -> None:
    owner = _package(tmp_path, "app", scripts={"build": "echo ok"}, lock_packages={})
    nested = tmp_path / "node_modules" / "dep"
    nested.mkdir(parents=True)
    (nested / "package.json").write_text(
        json.dumps({"scripts": {"build": "echo hidden"}}), encoding="utf-8"
    )
    assert subject._package_candidates(tmp_path, "build") == (owner,)
    assert subject._package_candidates(tmp_path, "missing") == ()

    invalid = tmp_path / "bad"
    invalid.mkdir()
    (invalid / "package.json").write_text("{", encoding="utf-8")
    result = subject.derive_npm_run_authority(tmp_path, "build", chain=("target",))
    assert result.unresolved
    assert result.package_executables == ()


def test_string_bin_names_cannot_be_inferred_from_package_location(tmp_path: Path) -> None:
    directory = _package(
        tmp_path,
        "app",
        scripts={"build": "tool"},
        lock_packages={"node_modules/tool": {"version": "1", "bin": "cli.js"}},
    )
    manifest = directory / "package.json"
    manifest.write_text(
        json.dumps({"scripts": {"build": "tool"}, "devDependencies": {"tool": "1"}}),
        encoding="utf-8",
    )
    result = subject.derive_npm_run_authority(
        tmp_path, "build", chain=("target",), package_dir=directory
    )
    assert result.unresolved
    assert result.package_executables == ()
    assert result.host_tools == ()


def test_unavailable_producer_cannot_activate_local_npm_source_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = _package(tmp_path, "app", scripts={"build": "echo okay"}, lock_packages={})
    baseline = subject.derive_npm_run_authority(
        tmp_path, "build", chain=("target",), package_dir=directory
    )
    assert baseline.unresolved
    assert baseline.source_identities

    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise ValueError("producer input unavailable")

    monkeypatch.setattr(subject, "observe_npm_project_declarations", unavailable)
    result = subject.derive_npm_run_authority(
        tmp_path, "build", chain=("target",), package_dir=directory
    )
    assert result.unresolved
    assert result.host_tools == ()
    assert result.package_executables == ()


@pytest.mark.parametrize(
    "body", ["sh -c '$TOOL'", "env MODE=one rm", "npm --prefix other run build"]
)
def test_literal_native_launcher_does_not_hide_unproven_operands(tmp_path: Path, body: str) -> None:
    directory = _package(tmp_path, "app", scripts={"build": "echo okay"}, lock_packages={})
    baseline = subject.derive_npm_run_authority(
        tmp_path, "build", chain=("target",), package_dir=directory
    )
    assert baseline.unresolved
    assert baseline.source_identities
    (directory / "package.json").write_text(
        json.dumps({"scripts": {"build": body}}), encoding="utf-8"
    )
    result = subject.derive_npm_run_authority(
        tmp_path, "build", chain=("target",), package_dir=directory
    )
    assert result.unresolved


def test_cached_npm_script_diamond_has_one_shared_work_budget(tmp_path: Path) -> None:
    directory = _package(
        tmp_path,
        "app",
        scripts={
            "root": "npm run middle && npm run middle",
            "middle": "npm run leaf && npm run leaf",
            "leaf": "git --version",
        },
        lock_packages={},
    )
    baseline = subject.derive_npm_run_authority(
        tmp_path, "root", chain=("target",), package_dir=directory
    )
    assert baseline.unresolved
    assert baseline.source_identities
    assert "git" in baseline.host_tools
    with hashmarks_source_scope(tmp_path) as scope:
        captured = subject.NpmSourceObservations(tmp_path, scope, work_remaining=3)
        result = subject.derive_npm_run_authority(
            tmp_path, "root", chain=("target",), package_dir=directory, observations=captured
        )
    assert result.unresolved
    assert any("work bound exhausted" in reason for reason in result.unresolved)
    assert "npm" in result.host_tools


def test_closed_original_source_scope_cannot_reuse_cached_npm_observations(tmp_path: Path) -> None:
    directory = _package(tmp_path, "app", scripts={"build": "echo okay"}, lock_packages={})
    with hashmarks_source_scope(tmp_path) as scope:
        captured = subject.NpmSourceObservations(tmp_path, scope)
        baseline = subject.derive_npm_run_authority(
            tmp_path, "build", chain=("target",), package_dir=directory, observations=captured
        )
        assert baseline.unresolved
    assert baseline.source_identities
    result = subject.derive_npm_run_authority(
        tmp_path, "build", chain=("target",), package_dir=directory, observations=captured
    )
    assert result.unresolved
    assert result.host_tools == ()
    assert result.package_executables == ()


@pytest.mark.parametrize("operation", ["ci", "install", "version"])
def test_native_npm_lifecycle_operations_are_not_target_free_source_success(operation: str) -> None:
    assert subject.npm_run_script(("npm", "--version"), 0) is None
    with pytest.raises(ValueError, match="unproven"):
        subject.npm_run_script(("npm", operation), 0)


@pytest.mark.process
def test_native_npm_multiline_cd_does_not_qualify_original_package_context(tmp_path: Path) -> None:
    npm = shutil.which("npm")
    if npm is None or not Path("/bin/sh").exists():
        pytest.skip("Native POSIX npm is required for package context proof")
    directory = _package(
        tmp_path,
        "app",
        scripts={"build": "npm run child", "child": "printf ORIGINAL_CONTEXT"},
        lock_packages={},
    )
    baseline = subject.derive_npm_run_authority(
        tmp_path, "build", chain=("target",), package_dir=directory
    )
    assert baseline.source_identities
    nested = directory / "nested"
    nested.mkdir()
    (nested / "package.json").write_text(
        json.dumps({"scripts": {"child": "printf NESTED_CONTEXT_CANARY"}}), encoding="utf-8"
    )
    (directory / "package.json").write_text(
        json.dumps(
            {"scripts": {"build": "cd nested\nnpm run child", "child": "printf ORIGINAL_CONTEXT"}}
        ),
        encoding="utf-8",
    )
    result = subject.derive_npm_run_authority(
        tmp_path, "build", chain=("target",), package_dir=directory
    )
    assert any("cd" in issue and "unproven" in issue for issue in result.unresolved)
    native = subprocess.run(
        [npm, "--ignore-scripts=false", "--script-shell=/bin/sh", "--silent", "run", "build"],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert native.returncode == 0, native.stderr
    assert "NESTED_CONTEXT_CANARY" in native.stdout
    assert "ORIGINAL_CONTEXT" not in native.stdout


@pytest.mark.process
def test_declared_native_npm_hooks_remain_unproven_without_a_lifecycle_interpreter(
    tmp_path: Path,
) -> None:
    npm = shutil.which("npm")
    if npm is None or not Path("/bin/sh").exists():
        pytest.skip("Native POSIX npm is required for lifecycle proof")
    directory = _package(tmp_path, "app", scripts={"verify": "printf MAIN"}, lock_packages={})
    baseline = subject.derive_npm_run_authority(
        tmp_path, "verify", chain=("target",), package_dir=directory
    )
    assert baseline.source_identities
    (directory / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "preverify": "printf PRE",
                    "verify": "printf MAIN",
                    "postverify": "printf POST",
                }
            }
        ),
        encoding="utf-8",
    )
    result = subject.derive_npm_run_authority(
        tmp_path, "verify", chain=("target",), package_dir=directory
    )
    assert any("lifecycle" in issue for issue in result.unresolved)
    assert any("script-shell" in issue for issue in result.unresolved)
    native = subprocess.run(
        [npm, "--ignore-scripts=false", "--script-shell=/bin/sh", "--silent", "run", "verify"],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert native.returncode == 0, native.stderr
    assert native.stdout == "PREMAINPOST"


def test_consumer_owned_provider_contract_validates_shape() -> None:
    assert subject._bin_provider_contract({}) == {}
    with pytest.raises(ValueError, match="must be an object"):
        subject._bin_provider_contract({"ohGoonExecutionAuthority": []})
    with pytest.raises(ValueError, match="packageBinProviders must be an object"):
        subject._bin_provider_contract({"ohGoonExecutionAuthority": {"packageBinProviders": []}})
    for providers in ({"": "pkg"}, {"x": ""}, {1: "pkg"}, {"x": 1}):
        with pytest.raises(ValueError, match="non-empty strings"):
            subject._bin_provider_contract(
                {"ohGoonExecutionAuthority": {"packageBinProviders": providers}}
            )


def test_exact_quoted_command_spelling_cannot_be_dropped_as_shell_punctuation(
    tmp_path: Path,
) -> None:
    directory = _package(tmp_path, "app", scripts={"build": "curl"}, lock_packages={})
    baseline = subject.derive_npm_run_authority(
        tmp_path, "build", chain=("target",), package_dir=directory
    )
    assert baseline.unresolved
    assert baseline.source_identities
    assert "curl" in baseline.host_tools
    (directory / "package.json").write_text(
        json.dumps({"scripts": {"build": "'curl)'"}}), encoding="utf-8"
    )
    result = subject.derive_npm_run_authority(
        tmp_path, "build", chain=("target",), package_dir=directory
    )
    assert result.unresolved
    assert "curl" not in result.host_tools


def test_context_loading_rejects_missing_and_malformed_authority_sources(tmp_path: Path) -> None:
    directory = tmp_path / "app"
    directory.mkdir()
    with pytest.raises(ValueError, match="no package.json"):
        subject._load_context(tmp_path, directory)
    (directory / "package.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="no package-lock.json"):
        subject._load_context(tmp_path, directory)
    (directory / "package-lock.json").write_text(
        '{"lockfileVersion":3,"packages":{}}', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="no scripts object"):
        subject._load_context(tmp_path, directory)
    (directory / "package.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        subject._load_context(tmp_path, directory)


def test_context_selection_rejects_missing_and_ambiguous_script_owners(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unproven"):
        subject._select_context(tmp_path, "build", None)
    one = _package(tmp_path, "one", scripts={"build": "echo 1"}, lock_packages={})
    two = _package(tmp_path, "two", scripts={"build": "echo 2"}, lock_packages={})
    with pytest.raises(ValueError, match="ambiguous"):
        subject._select_context(tmp_path, "build", None)
    assert subject._select_context(tmp_path, "build", one).directory == one.resolve()
    assert two.is_dir()


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        (("npm",), None),
        (("npm", "--silent"), None),
        (("npm", "test"), "test"),
        (("npm", "run", "--silent", "build"), "build"),
        (("npm", "run-script", "--", "build"), "build"),
        (("npm", "--version"), None),
    ],
)
def test_npm_run_parser_is_static_and_option_aware(
    words: tuple[str, ...], expected: str | None
) -> None:
    assert subject.npm_run_script(words, 0) == expected


@pytest.mark.parametrize(
    "words",
    [
        ("npm", "--prefix", "frontend", "run", "build"),
        ("npm", "--unknown-option", "run", "build"),
        ("npm", "run", "--workspace", "other", "build"),
        ("npm", "exec", "build"),
    ],
)
def test_unproven_npm_operands_cannot_become_target_free_success(words: tuple[str, ...]) -> None:
    assert subject.npm_run_script(("npm", "run", "build"), 0) == "build"
    with pytest.raises(ValueError, match="unproven"):
        subject.npm_run_script(words, 0)


def test_script_scanner_rejects_dynamic_nested_npx_paths_tokens_cycles_and_missing(
    tmp_path: Path,
) -> None:
    directory = _package(
        tmp_path,
        "app",
        scripts={
            "root": "npm run '$TARGET' && npx tool && ./local && bad@token && npm run cycle",
            "cycle": "npm run cycle",
            "sub": "echo $(npm run missing)",
        },
        lock_packages={},
    )
    root = subject.derive_npm_run_authority(
        tmp_path, "root", chain=("root",), package_dir=directory
    )
    text = "\n".join(root.unresolved)
    assert "dynamic npm script name" in text
    assert "npx execution" in text
    assert "executable path" in text
    assert "executable token" in text
    assert "npm script cycle" in text
    sub = subject.derive_npm_run_authority(tmp_path, "sub", chain=("sub",), package_dir=directory)
    assert sub.unresolved


def test_package_provider_contract_rejects_unavailable_selected_provider(tmp_path: Path) -> None:
    directory = _package(
        tmp_path,
        "app",
        scripts={"test": "runner"},
        lock_packages={
            "node_modules/a": {"version": "1", "bin": {"runner": "a"}},
            "node_modules/b": {"version": "1", "bin": {"runner": "b"}},
        },
    )
    payload = json.loads((directory / "package.json").read_text())
    payload["dependencies"] = {"a": "1", "b": "1"}
    payload["ohGoonExecutionAuthority"] = {"packageBinProviders": {"runner": "missing"}}
    (directory / "package.json").write_text(json.dumps(payload), encoding="utf-8")
    authority = subject.derive_npm_run_authority(
        tmp_path, "test", chain=("test",), package_dir=directory
    )
    assert any("selects unavailable provider" in item for item in authority.unresolved)


def test_unparseable_and_control_only_scripts_do_not_invent_host_authority(tmp_path: Path) -> None:
    directory = _package(
        tmp_path,
        "app",
        scripts={"bad": "echo 'unterminated", "control": "then ; do ; fi"},
        lock_packages={},
    )
    bad = subject.derive_npm_run_authority(tmp_path, "bad", chain=("bad",), package_dir=directory)
    assert bad.unresolved
    control = subject.derive_npm_run_authority(
        tmp_path, "control", chain=("control",), package_dir=directory
    )
    assert control.package_executables == ()
