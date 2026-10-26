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

from oh_goon.repository_tooling.release.build_wheel import (
    _archive_catalogs,
    build_wheel,
    staged_template_catalogs,
)
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.integrations.source_snapshot import snapshot_source_directory
from oh_goon.services.template_plan_drafts import (
    AGENT_REVIEW_EXAMPLE,
    agent_review_companion_files,
)

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
    forced = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"].get(
        "force-include", {}
    )
    assert "../generated/release/static" not in forced

    backend = tmp_path / "backend"
    backend.mkdir()
    module = _hatch_build_module(monkeypatch)
    assert (
        module.release_static_inclusion(project_root=backend, version="editable") == {}
    )
    assert module.release_catalog_inclusion(version="editable") == {}
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


def _python_goon_release_source(root: Path) -> Path:
    package = root / "templates/goons/ml/train"
    package.mkdir(parents=True)
    (root / "templates/plans").mkdir()
    (package / "goon.yml").write_text(
        "runtime: python\nsteps:\n- run: train\n  artifacts:\n"
        "  - path: assets/result.json\n    retention: evidence\n",
        encoding="utf-8",
    )
    for relative in (
        "README.md",
        "pyproject.toml",
        "uv.lock",
        "src/train.py",
        "tests/test_train.py",
        "assets/input.json",
        "assets/result.json",
        "goon.lock",
        ".venv/bin/python",
        ".pytest_cache/nodeids",
        "__pycache__/train.pyc",
        "src/train.egg-info/PKG-INFO",
        "train-0.1.dist-info/METADATA",
        ".eggs/dependency.egg/EGG-INFO/PKG-INFO",
        "build/recipe.txt",
        "dist/fixture.json",
    ):
        path = package / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("source\n", encoding="utf-8")
    return package


def test_release_catalog_capture_preserves_authored_output_siblings_and_scopes_scratch(
    tmp_path: Path,
) -> None:
    package = _python_goon_release_source(tmp_path)
    with staged_template_catalogs(tmp_path) as stage:
        staged = stage / "goons/ml/train"
        assert (staged / "assets/input.json").read_text() == "source\n"
        assert (staged / "src/train.py").is_file()
        assert (staged / "tests/test_train.py").is_file()
        assert (staged / "build/recipe.txt").read_text() == "source\n"
        assert (staged / "dist/fixture.json").read_text() == "source\n"
        for relative in (
            "assets/result.json",
            "goon.lock",
            ".venv",
            ".pytest_cache",
            "__pycache__",
            "src/train.egg-info",
            "train-0.1.dist-info",
            ".eggs",
        ):
            assert not (staged / relative).exists()
        assert (package / "assets/result.json").exists()
        assert (package / "src/train.egg-info/PKG-INFO").exists()
    assert not stage.exists()


def test_release_catalog_capture_preserves_crlf_yaml_source_identity(
    tmp_path: Path,
) -> None:
    package = _python_goon_release_source(tmp_path)
    source = package / "goon.yml"
    expected = source.read_bytes().replace(b"\n", b"\r\n")
    source.write_bytes(expected)

    with staged_template_catalogs(tmp_path) as stage:
        assert (stage / "goons/ml/train/goon.yml").read_bytes() == expected


def test_release_captures_exact_agent_companions_and_refuses_symlink_resources(
    tmp_path: Path,
) -> None:
    _python_goon_release_source(tmp_path)
    package = tmp_path / "templates/goons/agents/agentic-review-repair-loop"
    shutil.copytree(ROOT / "templates/goons/agents/agentic-review-repair-loop", package)
    files = agent_review_companion_files(ROOT)
    for relative, content in files.items():
        path = tmp_path / "examples" / AGENT_REVIEW_EXAMPLE / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
    with staged_template_catalogs(tmp_path) as stage:
        for relative, content in files.items():
            assert (
                stage / "examples" / AGENT_REVIEW_EXAMPLE / relative
            ).read_bytes() == content.encode("utf-8")
    skill = tmp_path / "examples" / AGENT_REVIEW_EXAMPLE / "skills/repair/SKILL.md"
    skill.unlink()
    private = tmp_path / "private.md"
    private.write_text("private source\n")
    skill.symlink_to(private)
    with pytest.raises(FleetPlanInvalid, match="symlink"):
        with staged_template_catalogs(tmp_path):
            pytest.fail(
                "linked authoring resources must refuse before native packaging"
            )
    assert private.read_text() == "private source\n"


def test_standard_wheel_rejects_missing_or_changed_catalog_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _hatch_build_module(monkeypatch)
    monkeypatch.delenv("OH_GOON_RELEASE_CATALOG_ARCHIVE", raising=False)
    monkeypatch.delenv("OH_GOON_RELEASE_CATALOG_ARCHIVE_SHA256", raising=False)
    with pytest.raises(RuntimeError, match="requires admitted template catalog source"):
        module.release_catalog_inclusion(version="standard")
    _python_goon_release_source(tmp_path)
    with staged_template_catalogs(tmp_path) as stage:
        archive, digest = _archive_catalogs(stage)
        monkeypatch.setenv("OH_GOON_RELEASE_CATALOG_ARCHIVE", str(archive))
        monkeypatch.setenv("OH_GOON_RELEASE_CATALOG_ARCHIVE_SHA256", digest)
        with archive.open("ab") as stream:
            stream.write(b"changed delivery")
        with pytest.raises(RuntimeError, match="differs from admitted source"):
            module.release_catalog_inclusion(version="standard")
        assert not (archive.parent / "hatch-catalogs").exists()


def test_release_catalog_refuses_yaml_that_changed_after_compilation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.repository_tooling.release import build_wheel as module

    package = _python_goon_release_source(tmp_path)

    def snapshot(
        source: Path, destination: Path, *, label: str, exclude_paths: tuple[str, ...]
    ) -> SnapshotIdentity:
        if source.name == "goons":
            path = package / "goon.yml"
            path.write_text(path.read_text().replace("run: train", "run: changed"))
        return snapshot_source_directory(
            source, destination, label=label, exclude_paths=exclude_paths
        )

    monkeypatch.setattr(module, "snapshot_source_directory", snapshot)
    with pytest.raises(
        FleetPlanInvalid, match="changed while release catalog source was captured"
    ):
        with staged_template_catalogs(tmp_path):
            pytest.fail("changed source must not reach the native wheel builder")


def test_standard_wheel_refuses_catalog_source_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _python_goon_release_source(tmp_path)
    (package / "assets/link.json").symlink_to("input.json")
    module = _hatch_build_module(monkeypatch)
    with staged_template_catalogs(tmp_path) as stage:
        archive, digest = _archive_catalogs(stage)
        monkeypatch.setenv("OH_GOON_RELEASE_CATALOG_ARCHIVE", str(archive))
        monkeypatch.setenv("OH_GOON_RELEASE_CATALOG_ARCHIVE_SHA256", digest)
        with pytest.raises(RuntimeError, match="non-regular source"):
            module.release_catalog_inclusion(version="standard")


@pytest.mark.parametrize("relative", ("README.md:Zone.Identifier", "copied.patch"))
def test_release_catalog_refuses_transport_contamination_without_deleting_source(
    tmp_path: Path, relative: str
) -> None:
    package = _python_goon_release_source(tmp_path)
    with staged_template_catalogs(tmp_path) as stage:
        assert (stage / "goons/ml/train/goon.yml").is_file()
    contamination = package / relative
    contamination.write_text("transport\n", encoding="utf-8")

    with pytest.raises(FleetPlanInvalid, match="transport-clean explicitly"):
        with staged_template_catalogs(tmp_path):
            pytest.fail("contaminated source must not reach native packaging")

    assert contamination.read_text(encoding="utf-8") == "transport\n"


@pytest.mark.process
def test_native_wheel_includes_captured_python_project_without_generated_outputs(
    tmp_path: Path,
) -> None:
    package = _python_goon_release_source(tmp_path)
    backend = tmp_path / "backend"
    source = backend / "src/oh_goon"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("def main(): return 0\n", encoding="utf-8")
    (backend / "pyproject.toml").write_text(
        '[project]\nname="oh-goon"\nversion="1.0.0"\nrequires-python=">=3.12"\n'
        '[project.scripts]\noh-goon="oh_goon:main"\n'
        '[build-system]\nrequires=["hatchling>=1.32.0"]\nbuild-backend="hatchling.build"\n'
        '[tool.hatch.build.targets.wheel]\npackages=["src/oh_goon"]\n'
        '[tool.hatch.build.targets.wheel.hooks.custom]\npath="hatch_build.py"\n',
        encoding="utf-8",
    )
    shutil.copyfile(ROOT / "backend/hatch_build.py", backend / "hatch_build.py")
    static = tmp_path / "generated/release/static"
    static.mkdir(parents=True)
    (static / "index.html").write_text("UI\n", encoding="utf-8")
    (tmp_path / "VERSION").write_text("1.0.0\n", encoding="utf-8")

    assert build_wheel(tmp_path, offline=True) == 0

    import zipfile

    wheel = next((tmp_path / "generated/release/dist").glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        prefix = "oh_goon/templates/goons/ml/train/"
        assert archive.read(prefix + "assets/input.json") == b"source\n"
        assert prefix + "src/train.py" in names
        assert prefix + "tests/test_train.py" in names
        assert prefix + "uv.lock" in names
        assert archive.read(prefix + "build/recipe.txt") == b"source\n"
        assert archive.read(prefix + "dist/fixture.json") == b"source\n"
        assert prefix + "assets/result.json" not in names
        assert prefix + "goon.lock" not in names
        assert not any("/.venv/" in name or "/__pycache__/" in name for name in names)
        assert not any(
            ".egg-info/" in name or ".dist-info/" in name or "/.eggs/" in name
            for name in names
            if name.startswith(prefix)
        )
    assert (package / "assets/result.json").exists()


@pytest.mark.process
def test_installed_wheel_initializes_complete_agent_workflow_without_checkout_resources(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    backend = root / "backend"
    backend.mkdir(parents=True)
    shutil.copytree(
        ROOT / "backend/src",
        backend / "src",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name in ("pyproject.toml", "hatch_build.py"):
        shutil.copyfile(ROOT / "backend" / name, backend / name)
    shutil.copyfile(ROOT / "VERSION", root / "VERSION")
    snapshot_source_directory(
        ROOT / "templates", root / "templates", label="release fixture"
    )
    for relative, content in agent_review_companion_files(ROOT).items():
        destination = root / "examples" / AGENT_REVIEW_EXAMPLE / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content.encode("utf-8"))
    static = root / "generated/release/static"
    static.mkdir(parents=True)
    (static / "index.html").write_text("UI\n")
    assert build_wheel(root, offline=True) == 0
    wheel = next((root / "generated/release/dist").glob("*.whl"))

    environment = tmp_path / "installed"
    native_environment = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(environment)}
    prepared = subprocess.run(
        [
            "uv",
            "sync",
            "--locked",
            "--offline",
            "--no-default-groups",
            "--no-install-project",
            "--project",
            str(ROOT / "backend"),
            "--python",
            sys.executable,
        ],
        env=native_environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert prepared.returncode == 0, prepared.stderr
    installed = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--offline",
            "--no-deps",
            "--python",
            str(environment / "bin/python"),
            str(wheel),
        ],
        env=native_environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert installed.returncode == 0, installed.stderr
    repository = tmp_path / "target"
    (repository / ".git").mkdir(parents=True)
    league = tmp_path / "league.yml"
    import yaml

    league.write_text(yaml.safe_dump({"version": 1, "folders": [str(repository)]}))
    native_environment["OH_GOON_LEAGUE_FILE"] = str(league)
    native_environment.pop("PYTHONPATH", None)
    shutil.rmtree(root / "templates")
    shutil.rmtree(root / "examples")
    shutil.rmtree(backend / "src")
    output = tmp_path / "workflow"
    result = subprocess.run(
        [
            str(environment / "bin/oh-goon"),
            "templates",
            "init",
            "agents/agentic-review-repair-loop",
            "--kind",
            "plan",
            "--output",
            str(output),
            "--repository",
            str(repository),
        ],
        cwd=tmp_path,
        env=native_environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    for relative in (
        ".goonskills.yml",
        "skills/adjudicate/SKILL.md",
        "skills/repair/SKILL.md",
    ):
        assert (output / relative).read_bytes() == (
            ROOT / "examples" / AGENT_REVIEW_EXAMPLE / relative
        ).read_bytes()
    selected = yaml.safe_load((output / "run.yml").read_text())
    assert selected["inputs"]["repo"] == "local:target"
    assert selected["agent"]["repository_root"] == str(repository.parent)
    assert selected["agent"]["model_profile"] == "REQUIRED_MODEL_PROFILE"
    inspected = subprocess.run(
        [
            str(environment / "bin/oh-goon"),
            "config",
            "inspect",
            str(output / "run.yml"),
            "--json",
        ],
        cwd=tmp_path,
        env=native_environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert inspected.returncode == 0, inspected.stderr + inspected.stdout


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
            'if [ "$1" = run ]; then\n'
            '  printf "uv:build\\n" >> "$RELEASE_COMMANDS"\n'
            "  cp generated/release/static/index.html "
            "generated/release/dist/wheel-ui-proof\n"
            "fi\n"
        ),
        backend_bin / "oh-goon-release-version": "#!/bin/sh\nexit 0\n",
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
