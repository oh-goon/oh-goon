from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tomllib
import types

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _hatch_build_module(monkeypatch: pytest.MonkeyPatch):
    interface = types.ModuleType("hatchling.builders.hooks.plugin.interface")
    interface.BuildHookInterface = type("BuildHookInterface", (), {})
    for name in (
        "hatchling",
        "hatchling.builders",
        "hatchling.builders.hooks",
        "hatchling.builders.hooks.plugin",
    ):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "hatchling.builders.hooks.plugin.interface", interface)

    path = ROOT / "backend/hatch_build.py"
    spec = importlib.util.spec_from_file_location("_oh_goon_test_hatch_build", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_editable_backend_materialization_has_no_release_static_source_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pyproject = tomllib.loads((ROOT / "backend/pyproject.toml").read_text(encoding="utf-8"))
    forced = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert "../generated/release/static" not in forced

    backend = tmp_path / "backend"
    backend.mkdir()
    module = _hatch_build_module(monkeypatch)
    assert module.release_static_inclusion(project_root=backend, version="editable") == {}
    assert not (tmp_path / "generated/release/static").exists()


def test_standard_release_wheel_requires_built_ui_at_release_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = tmp_path / "backend"
    backend.mkdir()
    module = _hatch_build_module(monkeypatch)

    with pytest.raises(RuntimeError, match="standard release wheel requires"):
        module.release_static_inclusion(project_root=backend, version="standard")

    static = tmp_path / "generated/release/static"
    static.mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html>", encoding="utf-8")
    assert module.release_static_inclusion(project_root=backend, version="standard") == {
        str(static): "oh_goon/static"
    }


def test_release_staging_is_owned_by_release_script_not_make_bootstrap_target() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    release_script = (ROOT / "scripts/release/build_release.sh").read_text(encoding="utf-8")
    assert "\nrelease-static-dir:" not in makefile
    assert "build-release: release-static-dir" not in makefile
    assert "release-accept: release-static-dir" not in makefile
    assert 'mkdir -p "$ROOT/generated/release/static" "$ROOT/generated/release/dist"' in release_script
    assert "cp -a frontend/dist/. generated/release/static/" in release_script
    assert "trap cleanup_failed_release EXIT" in release_script
    assert "clear_release_outputs" in release_script


def test_source_bootstrap_layers_do_not_compensate_for_release_static_contract() -> None:
    surfaces = {
        "Makefile": ROOT / "Makefile",
        "source normalization": ROOT / "backend/src/oh_goon/repository_tooling/source_normalization.py",
        "source ZIP": ROOT / "backend/src/oh_goon/repository_tooling/release/package_slim.py",
        "package config": ROOT / "backend/pyproject.toml",
    }
    for label, path in surfaces.items():
        text = path.read_text(encoding="utf-8")
        assert "generated/release/static" not in text, label

    hook = (ROOT / "backend/hatch_build.py").read_text(encoding="utf-8")
    release = (ROOT / "scripts/release/build_release.sh").read_text(encoding="utf-8")
    assert "generated/release/static" in hook
    assert 'version != "standard"' in hook
    assert "generated/release/static" in release