from __future__ import annotations

import json
from pathlib import Path

import pytest

from oh_goon.integrations import package_script_execution_authority as subject


def _package(root: Path, name: str, *, scripts: object, lock_packages: object) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "package.json").write_text(
        json.dumps({"name": name, "scripts": scripts}) + "\n", encoding="utf-8"
    )
    (directory / "package-lock.json").write_text(
        json.dumps({"packages": lock_packages}) + "\n", encoding="utf-8"
    )
    return directory


def test_package_candidate_discovery_ignores_invalid_and_node_modules(tmp_path: Path) -> None:
    owner = _package(tmp_path, "app", scripts={"build": "echo ok"}, lock_packages={})
    invalid = tmp_path / "bad"
    invalid.mkdir()
    (invalid / "package.json").write_text("{", encoding="utf-8")
    nested = tmp_path / "node_modules" / "dep"
    nested.mkdir(parents=True)
    (nested / "package.json").write_text(
        json.dumps({"scripts": {"build": "echo hidden"}}), encoding="utf-8"
    )
    assert subject._package_candidates(tmp_path, "build") == (owner,)
    assert subject._package_candidates(tmp_path, "missing") == ()


@pytest.mark.parametrize(
    ("package_path", "raw", "expected"),
    [
        ("pkg", {"version": "1", "bin": {"x": "x.js"}}, ()),
        ("node_modules/a/node_modules/b", {"version": "1", "bin": {"x": "x.js"}}, ()),
        ("node_modules/a", [], ()),
        ("node_modules/a", {"version": "", "bin": {"x": "x.js"}}, ()),
        ("node_modules/a", {"version": 1, "bin": {"x": "x.js"}}, ()),
        ("node_modules/a", {"version": "1", "bin": 7}, ()),
    ],
)
def test_package_bin_entries_rejects_non_authoritative_lock_shapes(
    package_path: str, raw: object, expected: tuple[object, ...]
) -> None:
    assert subject._package_bin_entries(package_path, raw, package_directory="app") == expected


def test_package_bin_entries_supports_string_bin_and_filters_bad_entries() -> None:
    rows = subject._package_bin_entries(
        "node_modules/tool",
        {"version": "1.2.3", "bin": "cli.js"},
        package_directory="app",
    )
    assert [(row.bin_name, row.bin_target, row.integrity) for row in rows] == [
        ("tool", "cli.js", "")
    ]
    rows = subject._package_bin_entries(
        "node_modules/tool",
        {"version": "1", "integrity": 9, "bin": {"ok": "ok.js", 1: "bad", "bad": 2}},
        package_directory="app",
    )
    assert [(row.bin_name, row.bin_target) for row in rows] == [("ok", "ok.js")]


def test_lock_bin_index_and_package_names_fail_closed() -> None:
    with pytest.raises(ValueError, match="packages object"):
        subject._bin_rows({}, package_directory=".")
    rows = subject._bin_rows(
        {
            "packages": {
                7: {"version": "1", "bin": {"bad": "bad"}},
                "node_modules/@scope/pkg": {"version": "1", "bin": {"scoped": "x"}},
                "node_modules/plain": {"version": "1", "bin": {"plain": "y"}},
            }
        },
        package_directory="app",
    )
    assert set(rows) == {"plain", "scoped"}
    assert subject._package_name("node_modules/@scope/pkg/sub") == "@scope/pkg"
    assert subject._package_name("node_modules/plain/sub") == "plain"


def test_direct_packages_and_provider_contract_validate_shape() -> None:
    assert subject._direct_packages(
        {
            "dependencies": {"a": "1", 7: "bad"},
            "devDependencies": {"b": "1"},
            "optionalDependencies": ["ignored"],
        }
    ) == frozenset({"a", "b"})
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


def test_context_loading_rejects_missing_and_malformed_authority_sources(tmp_path: Path) -> None:
    directory = tmp_path / "app"
    directory.mkdir()
    with pytest.raises(ValueError, match="no package.json"):
        subject._load_context(tmp_path, directory)
    (directory / "package.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="no package-lock.json"):
        subject._load_context(tmp_path, directory)
    (directory / "package-lock.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="no scripts object"):
        subject._load_context(tmp_path, directory)
    (directory / "package.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot read"):
        subject._load_context(tmp_path, directory)


def test_context_selection_rejects_missing_and_ambiguous_script_owners(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not owned"):
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
        (("npm", "exec", "build"), None),
    ],
)
def test_npm_run_parser_is_static_and_option_aware(
    words: tuple[str, ...], expected: str | None
) -> None:
    assert subject.npm_run_script(words, 0) == expected


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
    assert any("npm script is missing" in item for item in sub.unresolved)


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
    assert any("unparseable npm script body" in item for item in bad.unresolved)
    control = subject.derive_npm_run_authority(
        tmp_path, "control", chain=("control",), package_dir=directory
    )
    assert control.package_executables == ()
