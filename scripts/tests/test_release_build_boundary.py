from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path
import sys
import tomllib
import types

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _hatch_build_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    interface = types.ModuleType("hatchling.builders.hooks.plugin.interface")
    setattr(interface, "BuildHookInterface", type("BuildHookInterface", (), {}))
    for name in (
        "hatchling",
        "hatchling.builders",
        "hatchling.builders.hooks",
        "hatchling.builders.hooks.plugin",
    ):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(
        sys.modules, "hatchling.builders.hooks.plugin.interface", interface
    )

    path = ROOT / "backend/hatch_build.py"
    spec = importlib.util.spec_from_file_location("_oh_goon_test_hatch_build", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_editable_backend_materialization_has_no_release_static_source_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pyproject = tomllib.loads(
        (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
    )
    forced = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert "../generated/release/static" not in forced

    backend = tmp_path / "backend"
    backend.mkdir()
    module = _hatch_build_module(monkeypatch)
    assert (
        module.release_static_inclusion(project_root=backend, version="editable") == {}
    )
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
    assert module.release_static_inclusion(
        project_root=backend, version="standard"
    ) == {str(static): "oh_goon/static"}


def test_release_staging_is_owned_by_release_script_not_make_bootstrap_target() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    release_script = (ROOT / "scripts/release/build_release.sh").read_text(
        encoding="utf-8"
    )
    assert "\nrelease-static-dir:" not in makefile
    assert "build-release: release-static-dir" not in makefile
    assert "release-accept: release-static-dir" not in makefile
    assert (
        'mkdir -p "$ROOT/generated/release/static" "$ROOT/generated/release/dist"'
        in release_script
    )
    assert "cp -a frontend/dist/. generated/release/static/" in release_script
    assert "trap cleanup_failed_release EXIT" in release_script
    assert "clear_release_outputs" in release_script


def test_source_bootstrap_layers_do_not_compensate_for_release_static_contract() -> (
    None
):
    surfaces = {
        "Makefile": ROOT / "Makefile",
        "source normalization": ROOT
        / "backend/src/oh_goon/repository_tooling/source_normalization.py",
        "source ZIP": ROOT
        / "backend/src/oh_goon/repository_tooling/release/package_slim.py",
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


@pytest.mark.parametrize("previous_ui", (None, "stale UI\n"))
@pytest.mark.parametrize("frontend_exit", (0, 7))
@pytest.mark.parametrize("action", (None, "assemble"))
def test_release_build_owns_fresh_ui_and_cleans_failed_outputs(
    tmp_path: Path, previous_ui: str | None, frontend_exit: int, action: str | None
) -> None:
    script = tmp_path / "scripts/release/build_release.sh"
    script.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/release/build_release.sh", script)
    if previous_ui is not None:
        frontend = tmp_path / "frontend/dist"
        frontend.mkdir(parents=True)
        (frontend / "index.html").write_text(previous_ui, encoding="utf-8")
    for directory in ("static", "dist"):
        output = tmp_path / "generated/release" / directory
        output.mkdir(parents=True)
        (output / "old-output").write_text("old release", encoding="utf-8")
    tools = tmp_path / "tools"
    tools.mkdir()
    backend_bin = tmp_path / "backend/.venv/bin"
    backend_bin.mkdir(parents=True)
    commands = tmp_path / "commands"
    for path, body in {
        tools / "make": (
            '#!/bin/sh\nprintf "make:%s\\n" "$1" >> "$RELEASE_COMMANDS"\n'
            'if [ "$1" = frontend-build ]; then\n'
            '  [ "$RELEASE_FRONTEND_EXIT" = 0 ] || exit "$RELEASE_FRONTEND_EXIT"\n'
            "  mkdir -p frontend/dist\n"
            '  printf "fresh UI\\n" > frontend/dist/index.html\n'
            "fi\n"
        ),
        tools / "uv": (
            '#!/bin/sh\nprintf "uv:%s\\n" "$1" >> "$RELEASE_COMMANDS"\n'
            'if [ "$1" = build ]; then\n'
            "  cp ../generated/release/static/index.html "
            "../generated/release/dist/wheel-ui-proof\n"
            "fi\n"
        ),
        backend_bin / "oh-goon-release-version": "#!/bin/sh\nexit 0\n",
        backend_bin / "oh-goon-release-artifact-check": "#!/bin/sh\nexit 0\n",
    }.items():
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
    result = subprocess.run(
        ("bash", str(script), *((action,) if action is not None else ())),
        env={
            **os.environ,
            "PATH": str(tools) + os.pathsep + os.defpath,
            "RELEASE_COMMANDS": str(commands),
            "RELEASE_FRONTEND_EXIT": str(frontend_exit),
        },
        check=False,
        capture_output=True,
        text=True,
    )
    observed = commands.read_text(encoding="utf-8").splitlines()
    assert observed.count("make:frontend-build") == 1
    assert observed.count("make:release-locks-check") == 1
    assert observed.index("make:release-locks-check") < observed.index("uv:sync")
    if action == "assemble":
        assert "make:generate" not in observed
        assert "make:release-check" not in observed
    else:
        assert (
            observed.count("make:generate") == observed.count("make:release-check") == 1
        )
        assert observed.index("make:generate") < observed.index("make:release-check")
    assert result.returncode == frontend_exit, result.stdout + result.stderr
    release = tmp_path / "generated/release"
    if frontend_exit:
        assert "uv:build" not in observed
        assert not (release / "static").exists()
        assert not (release / "dist").exists()
    else:
        assert (release / "static/index.html").read_text() == "fresh UI\n"
        assert (release / "dist/wheel-ui-proof").read_text() == "fresh UI\n"
        if action != "assemble":
            assert observed.index("make:release-check") < observed.index(
                "make:frontend-build"
            )
        assert observed.index("make:frontend-build") < observed.index("uv:build")


@pytest.mark.parametrize("arguments", (("unknown",), ("assemble", "unexpected")))
def test_unknown_release_action_rejects_before_cleanup_or_native_work(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    script = tmp_path / "scripts/release/build_release.sh"
    script.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/release/build_release.sh", script)
    stale = tmp_path / "generated/release/dist/previous-wheel.whl"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"retained previous artifact")
    result = subprocess.run(
        ("bash", str(script), *arguments),
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": os.defpath},
    )
    assert result.returncode == 2
    assert "usage:" in result.stderr
    assert stale.read_bytes() == b"retained previous artifact"
    assert not (tmp_path / "backend/.venv").exists()
