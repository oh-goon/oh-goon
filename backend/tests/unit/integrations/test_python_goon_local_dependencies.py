from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from textwrap import dedent

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.python_goon_projects import materialize_python_goon_project
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def _native_uv(project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    # Each operation starts without registry metadata or a previously built wheel.
    with tempfile.TemporaryDirectory(prefix="oh-goon-native-local-dependency-") as cache:
        return subprocess.run(
            ["uv", *arguments, "--offline", "--cache-dir", cache],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=30,
        )


def _native_lock(project: Path) -> None:
    result = _native_uv(project, "lock", "--python", sys.executable)
    assert result.returncode == 0, result.stderr


def _native_sync(project: Path) -> None:
    result = _native_uv(project, "sync", "--locked", "--python", sys.executable)
    assert result.returncode == 0, result.stderr


def _write_project(project: Path, *, dependencies: str = "[]", uv: str = "") -> str:
    project.mkdir(parents=True)
    (project / "goon.yml").write_text("runtime: python\nsteps:\n- run: local-application\n")
    (project / "README.md").write_text("# general/local-application\n")
    metadata = (
        dedent(f"""\
        [project]
        name = "local-application"
        version = "1.0.0"
        requires-python = ">=3.12"
        dependencies = {dependencies}
        [project.scripts]
        local-application = "local_application:main"
        [build-system]
        requires = ["uv_build>=0.10.0"]
        build-backend = "uv_build"
        """)
        + uv
    )
    (project / "pyproject.toml").write_text(metadata)
    module = project / "src/local_application"
    module.mkdir(parents=True)
    (module / "__init__.py").write_text("def main():\n    print('installed-application')\n")
    return metadata


def _build_native_wheel(source: Path, destination: Path, *, backend: bool = False) -> Path:
    source.mkdir()
    name = "local-backend" if backend else "outside-dependency"
    module = name.replace("-", "_")
    (source / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "1.0.0"\n'
        '[build-system]\nrequires = ["uv_build>=0.10.0"]\nbuild-backend = "uv_build"\n'
    )
    package = source / "src" / module
    package.mkdir(parents=True)
    implementation = (
        dedent("""\
        import zipfile
        from pathlib import Path

        def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
            name = "local_application-1.0.0-py3-none-any.whl"
            with zipfile.ZipFile(Path(wheel_directory) / name, "w") as wheel:
                wheel.writestr("local_application.py", "VALUE = 'native-backend-built'\\n")
                wheel.writestr("local_application-1.0.0.dist-info/METADATA", "Metadata-Version: 2.1\\nName: local-application\\nVersion: 1.0.0\\n")
                wheel.writestr("local_application-1.0.0.dist-info/WHEEL", "Wheel-Version: 1.0\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n")
                wheel.writestr("local_application-1.0.0.dist-info/RECORD", "")
            return name

        build_editable = build_wheel
        """)
        if backend
        else "VALUE = 'native-wheel-installed'\n"
    )
    (package / "__init__.py").write_text(implementation)
    result = _native_uv(source, "build", "--wheel", "--out-dir", str(destination))
    assert result.returncode == 0, result.stderr
    wheel = destination / f"{module}-1.0.0-py3-none-any.whl"
    assert wheel.is_file()
    return wheel


@pytest.mark.process
def test_native_local_flat_index_relocates_only_with_captured_wheels(tmp_path: Path) -> None:
    project = tmp_path / "project"
    metadata = _write_project(
        project,
        dependencies='["outside-dependency"]',
        uv='[tool.uv]\nfind-links = ["vendor"]\nno-index = true\n',
    )
    vendor = project / "vendor"
    _build_native_wheel(tmp_path / "dependency-source", vendor)
    _native_lock(project)
    _native_sync(project)
    resolver = FleetPlanResolver()
    captured = resolver.analyze_source(project / "goon.yml").python_projects()[0]
    receiver = tmp_path / "receiver"
    materialize_python_goon_project(captured.archive, captured.identity, receiver)
    shutil.rmtree(project)
    _native_sync(receiver)
    result = subprocess.run(
        [
            str(receiver / ".venv/bin/python"),
            "-c",
            "import outside_dependency; print(outside_dependency.VALUE)",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "native-wheel-installed"

    # Native uv accepts an external flat index, but it cannot be one standalone Goon.
    (receiver / "vendor").rename(tmp_path / "outside")
    (receiver / "pyproject.toml").write_text(metadata.replace('["vendor"]', '["../outside"]'))
    _native_lock(receiver)
    shutil.rmtree(receiver / ".venv")
    _native_sync(receiver)
    assert 'registry = "../outside"' in (receiver / "uv.lock").read_text()
    with pytest.raises(FleetPlanInvalid, match="dependency path escapes"):
        resolver.analyze_source(receiver / "goon.yml")


@pytest.mark.process
def test_native_build_requirement_file_url_must_not_depend_on_external_wheel(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    metadata = _write_project(project)
    _native_lock(project)
    assert FleetPlanResolver().analyze_source(project / "goon.yml").python_projects()
    wheel = _build_native_wheel(tmp_path / "backend-source", tmp_path / "outside", backend=True)
    (project / "pyproject.toml").write_text(
        metadata.replace(
            '["uv_build>=0.10.0"]', json.dumps([f"local-backend @ {wheel.as_uri()}"])
        ).replace('build-backend = "uv_build"', 'build-backend = "local_backend"')
    )
    _native_lock(project)
    # Installing the original proves this is a valid native build requirement.
    _native_sync(project)
    result = subprocess.run(
        [
            str(project / ".venv/bin/python"),
            "-c",
            "import local_application; print(local_application.VALUE)",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "native-backend-built"
    receiver = tmp_path / "receiver"
    shutil.copytree(project, receiver, ignore=shutil.ignore_patterns(".venv"))
    shutil.rmtree(wheel.parent)
    result = _native_uv(receiver, "sync", "--locked", "--python", sys.executable)
    assert result.returncode != 0
    assert wheel.name in result.stderr
    with pytest.raises(FleetPlanInvalid, match="dependency path escapes"):
        FleetPlanResolver().analyze_source(project / "goon.yml")


@pytest.mark.process
@pytest.mark.parametrize("editable", [False, True])
def test_native_selected_local_project_build_metadata_is_contained(
    tmp_path: Path, editable: bool
) -> None:
    project = tmp_path / "project"
    child = project / "member"
    child_metadata = _write_project(child)
    (project / "goon.yml").write_text("runtime: python\nsteps:\n- run: local-application\n")
    (project / "README.md").write_text("# general/root-application\n")
    (project / "pyproject.toml").write_text(
        '[project]\nname = "root-application"\nversion = "1.0.0"\n'
        'requires-python = ">=3.12"\ndependencies = ["local-application"]\n'
        "[tool.uv]\npackage = false\n[tool.uv.sources]\n"
        'local-application = {path = "member", editable = ' + str(editable).lower() + "}\n"
    )
    # Authored fixture metadata outside the native lock's project membership is
    # a resource, not another project to discover or interpret.
    unrelated = project / "tests/fixtures/invalid"
    unrelated.mkdir(parents=True)
    (unrelated / "pyproject.toml").write_text("intentionally invalid TOML")
    _native_lock(project)
    _native_sync(project)
    captured = FleetPlanResolver().analyze_source(project / "goon.yml").python_projects()[0]
    (child / "uv.toml").write_text('find-links = ["../../outside"]\n')
    with pytest.raises(FleetPlanInvalid, match="dependency path escapes"):
        FleetPlanResolver().analyze_source(project / "goon.yml")
    (child / "uv.toml").unlink()
    receiver = tmp_path / "receiver"
    materialize_python_goon_project(captured.archive, captured.identity, receiver)
    shutil.rmtree(project)
    _native_sync(receiver)

    wheel = _build_native_wheel(tmp_path / "backend-source", tmp_path / "outside", backend=True)
    child = receiver / "member"
    (child / "pyproject.toml").write_text(
        child_metadata.replace(
            '["uv_build>=0.10.0"]', json.dumps([f"local-backend @ {wheel.as_uri()}"])
        ).replace('build-backend = "uv_build"', 'build-backend = "local_backend"')
    )
    _native_lock(receiver)
    shutil.rmtree(receiver / ".venv")
    _native_sync(receiver)
    with pytest.raises(FleetPlanInvalid, match="dependency path escapes"):
        FleetPlanResolver().analyze_source(receiver / "goon.yml")


@pytest.fixture
def local_project(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "project"
    metadata = _write_project(project)
    _native_lock(project)
    assert FleetPlanResolver().analyze_source(project / "goon.yml").python_projects()
    return project, metadata


@pytest.mark.parametrize(
    "location",
    [
        "../outside",
        "C:/outside",
        r"C:\outside",
        r"\\server\outside",
        "file:///outside",
        "file://server/outside",
        "file:../outside",
        "file:%2e%2e%2foutside",
        "file:vendor/%2e%2e/%2e%2e/outside",
    ],
)
@pytest.mark.parametrize("field", ["find-links", "registry", "uv.toml"])
def test_local_index_escape_forms_refuse_before_execution(
    local_project: tuple[Path, str], location: str, field: str
) -> None:
    project, metadata = local_project
    if field == "uv.toml":
        (project / "uv.toml").write_text("find-links = " + json.dumps([location]) + "\n")
    elif field == "find-links":
        (project / "pyproject.toml").write_text(
            metadata + "\n[tool.uv]\nfind-links = " + json.dumps([location]) + "\n"
        )
    else:
        with (project / "uv.lock").open("a") as stream:
            stream.write(
                '\n[[package]]\nname = "outside"\nversion = "1.0.0"\nsource = {registry = '
                + json.dumps(location)
                + "}\n"
            )
    with pytest.raises(FleetPlanInvalid, match="dependency path escapes"):
        FleetPlanResolver().analyze_source(project / "goon.yml")


@pytest.mark.parametrize(
    "requirement",
    [
        "local-backend @ file:///outside/backend.whl",
        "local-backend[extra] @ file:../backend.whl ; python_version >= '3.12'",
        "local-backend@file:%2e%2e/backend.whl",
        "local-backend @ FILE://server/backend.whl",
    ],
)
def test_build_file_requirement_escape_syntax_refuses_before_execution(
    local_project: tuple[Path, str], requirement: str
) -> None:
    project, metadata = local_project
    (project / "pyproject.toml").write_text(
        metadata.replace('["uv_build>=0.10.0"]', json.dumps([requirement]))
    )
    with pytest.raises(FleetPlanInvalid, match="dependency path escapes"):
        FleetPlanResolver().analyze_source(project / "goon.yml")


def test_remote_locations_and_requirements_remain_native_authority(
    local_project: tuple[Path, str],
) -> None:
    project, metadata = local_project
    (project / "pyproject.toml").write_text(
        metadata.replace(
            '["uv_build>=0.10.0"]', '["backend @ https://example.invalid/backend.whl"]'
        )
        + '\n[tool.uv]\nfind-links = ["https://example.invalid/wheels"]\n'
        + 'index-url = "https://example.invalid/simple"\n'
    )
    with (project / "uv.lock").open("a") as stream:
        stream.write(
            '\n[[package]]\nname = "remote"\nversion = "1.0.0"\nsource = {registry = "https://example.invalid/simple"}\n'
        )
    assert FleetPlanResolver().analyze_source(project / "goon.yml").python_projects()


@pytest.mark.parametrize(
    "declaration",
    [
        '[dependency-groups]\ndev = ["backend @ file:../outside.whl"]\n',
        '[project.optional-dependencies]\nextra = ["backend @ file:../outside.whl"]\n',
        '[tool.uv.extra-build-dependencies]\nlocal-application = ["backend @ file:../outside.whl"]\n',
        '[tool.uv.extra-build-dependencies]\nlocal-application = [{requirement = "backend @ file:../outside.whl", match-runtime = true}]\n',
        '[tool.uv.sources]\nbackend = {git = "file:///outside/backend"}\n',
    ],
)
def test_other_native_local_dependency_declarations_are_contained(
    local_project: tuple[Path, str], declaration: str
) -> None:
    project, metadata = local_project
    (project / "pyproject.toml").write_text(metadata + "\n" + declaration)
    with pytest.raises(FleetPlanInvalid, match="dependency path escapes"):
        FleetPlanResolver().analyze_source(project / "goon.yml")
