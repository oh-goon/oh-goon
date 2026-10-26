"""Complete Python package fixtures using original runtime and native uv owners."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path
from textwrap import dedent
from typing import TypedDict

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.product_identity import repository_id, repository_subject_instance_id
from oh_goon.runtime import goon_templates
from oh_goon.runtime.league_folders import LeagueFolderStore

SHOWCASE = Path(__file__).resolve().parents[3] / "templates/goons/general/orders-taskflow"


def write_dynamic_metadata_probe(path: Path) -> Path:
    """A benign native backend whose metadata work exposes host execution."""
    path.mkdir(parents=True, exist_ok=True)
    marker = path / "native-metadata-marker"
    metadata = (
        '[project]\nname="native-metadata-probe"\ndynamic=["version"]\n'
        'requires-python=">=3.12"\n[build-system]\nrequires=[]\n'
        'build-backend="metadata_probe"\nbackend-path=["."]\n'
    )
    (path / "pyproject.toml").write_text(metadata)
    (path / "metadata_probe.py").write_text(
        dedent("""\
        from pathlib import Path
        import zipfile

        def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
            Path(__file__).with_name("native-metadata-marker").write_text("native backend ran")
            filename = "native_metadata_probe-1.0.0-py3-none-any.whl"
            with zipfile.ZipFile(Path(wheel_directory) / filename, "w") as wheel:
                wheel.writestr("native_metadata_probe.py", "VALUE = 1\\n")
                wheel.writestr("native_metadata_probe-1.0.0.dist-info/METADATA", "Metadata-Version: 2.1\\nName: native-metadata-probe\\nVersion: 1.0.0\\n")
                wheel.writestr("native_metadata_probe-1.0.0.dist-info/WHEEL", "Wheel-Version: 1.0\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n")
                wheel.writestr("native_metadata_probe-1.0.0.dist-info/RECORD", "")
            return filename

        build_editable = build_wheel
        """)
    )
    uv = shutil.which("uv")
    assert uv is not None, "native uv is required"
    with tempfile.TemporaryDirectory(prefix="oh-goon-dynamic-metadata-lock-") as directory:
        subprocess.run(
            [uv, "lock", "--project", str(path), "--offline"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
            env={
                "PATH": os.defpath,
                "HOME": directory,
                "UV_CACHE_DIR": directory,
                "UV_PYTHON": sys.executable,
                "UV_PYTHON_DOWNLOADS": "never",
            },
        )
    assert marker.is_file(), "the positive native metadata baseline must execute the backend"
    marker.unlink()
    (path / "pyproject.toml").write_text(metadata + "\n[dependency-groups]\nmetadata-audit=[]\n")
    return marker


def write_isolated_build_project(path: Path, *, stale_lock: bool = False) -> None:
    """Create a native self-contained backend whose writes expose the build boundary."""
    path.mkdir(parents=True)
    (path / "README.md").write_text("# general/isolated-build\n")
    (path / "goon.yml").write_text(
        "runtime: python\nuv: {lock_mode: locked}\nexecution: {purpose: cicd, trust_repository_code: false}\n"
        "steps:\n- run: isolated-build\n"
    )
    metadata = dedent("""\
        [project]
        name = "isolated-build"
        version = "1.0.0"
        requires-python = ">=3.12"
        [project.scripts]
        isolated-build = "isolated_app:main"
        [dependency-groups]
        dev = ["goon-dev-only"]
        [tool.uv.sources]
        goon-dev-only = {path = "vendor/goon_dev_only-1.0.0-py3-none-any.whl"}
        [build-system]
        requires = []
        build-backend = "backend"
        backend-path = ["."]
        """)
    (path / "pyproject.toml").write_text(metadata)
    vendor = path / "vendor"
    vendor.mkdir()
    with zipfile.ZipFile(vendor / "goon_dev_only-1.0.0-py3-none-any.whl", "w") as wheel:
        wheel.writestr("goon_dev_only.py", "DEVELOPMENT_ONLY = True\n")
        wheel.writestr(
            "goon_dev_only-1.0.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: goon-dev-only\nVersion: 1.0.0\n",
        )
        wheel.writestr(
            "goon_dev_only-1.0.0.dist-info/WHEEL",
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        wheel.writestr("goon_dev_only-1.0.0.dist-info/RECORD", "")
    (path / "isolated_app.py").write_text(
        dedent("""\
        import importlib.util
        import json
        from pathlib import Path

        def main():
            result = {
                "dev_dependency_absent": importlib.util.find_spec("goon_dev_only") is None,
                "installed_module": __file__,
            }
            Path("isolated-result.json").write_text(json.dumps(result))
            print("installed-command-ran")
        """)
    )
    (path / "backend.py").write_text(
        dedent("""\
        import json
        import os
        import socket
        import zipfile
        from pathlib import Path

        def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
            project = Path(__file__).parent
            observed = {"private_write": True}
            if "PROBE_CAPTURED_SOURCE" in os.environ:
                source = Path(os.environ["PROBE_CAPTURED_SOURCE"])
                assert (source / "pyproject.toml").is_file()
                try:
                    source.chmod(0o700)
                    (source / "backend-host-write").write_text("prohibited")
                except OSError:
                    observed["readonly_source"] = True
                else:
                    observed["readonly_source"] = False
                observed["host_secret_absent"] = not Path(os.environ["PROBE_HOST_SECRET"]).exists()
                try:
                    with socket.create_connection(("127.0.0.1", int(os.environ["PROBE_HOST_PORT"])), timeout=1):
                        observed["network_denied"] = False
                except OSError:
                    observed["network_denied"] = True
            (project / "backend-build.json").write_text(json.dumps(observed))
            filename = "isolated_build-1.0.0-py3-none-any.whl"
            with zipfile.ZipFile(Path(wheel_directory) / filename, "w") as wheel:
                wheel.writestr("isolated_app.py", (project / "isolated_app.py").read_text())
                wheel.writestr("isolated_build-1.0.0.dist-info/METADATA", "Metadata-Version: 2.1\\nName: isolated-build\\nVersion: 1.0.0\\n")
                wheel.writestr("isolated_build-1.0.0.dist-info/WHEEL", "Wheel-Version: 1.0\\nGenerator: test\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n")
                wheel.writestr("isolated_build-1.0.0.dist-info/entry_points.txt", "[console_scripts]\\nisolated-build = isolated_app:main\\n")
                wheel.writestr("isolated_build-1.0.0.dist-info/RECORD", "")
            return filename
        """)
    )
    with tempfile.TemporaryDirectory(prefix="oh-goon-native-lock-") as directory:
        subprocess.run(
            ["uv", "lock", "--offline", "--cache-dir", directory],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
    assert not (path / "backend-build.json").exists()
    if stale_lock:
        (path / "pyproject.toml").write_text(
            metadata.replace('version = "1.0.0"', 'version = "2.0.0"')
        )


class SharedPackageScheduleOptions(TypedDict):
    name: str
    repository_ids: tuple[str, ...]
    expected_repository_instance_ids: dict[str, str]
    plan_key: str
    expected_plan_revision: str
    expected_plan_id: str
    expected_execution_digest: str
    interval_minutes: int


@pytest.fixture
def execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[ApplicationRuntime, Path, Path]]:
    repository = tmp_path / "repositories/target"
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "runtime.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    LeagueFolderStore().replace((repository,))
    workflow = tmp_path / "workflow"
    package = workflow / "goons/general/orders"
    shutil.copytree(
        SHOWCASE,
        package,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".showcase"),
    )
    (package / "README.md").write_text("# general/orders\n\nRun a complete Python package.\n")
    plan = workflow / "plan.yml"
    plan.write_text("goons:\n- import: general/orders\n  from: goons\n")
    runtime = ApplicationRuntime.build(standard_local=True)
    runtime.locations.add(repository.parent)
    try:
        yield runtime, repository, plan
    finally:
        runtime.shutdown(wait_for_runs=True)


@pytest.fixture
def shared_execution(
    execution: tuple[ApplicationRuntime, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[ApplicationRuntime, Path, Path, SharedPackageScheduleOptions]]:
    previous, repository, plan = execution
    previous.shutdown(wait_for_runs=True)
    # Supply a complete installed-catalog fixture to the original source provider;
    # compilation, capture, persistence and native execution retain their real owners.
    monkeypatch.setattr(
        goon_templates, "default_goon_templates_root", lambda: plan.parent / "goons"
    )
    runtime = ApplicationRuntime.build(standard_local=True)
    document = runtime.shared_plan.save("orders", "goons:\n- general/orders\n")
    preview = runtime.shared_plan.lock_preview(document.plan_key)
    options: SharedPackageScheduleOptions = {
        "name": "Orders",
        "repository_ids": (repository_id(repository),),
        "expected_repository_instance_ids": {
            repository_id(repository): repository_subject_instance_id(repository),
        },
        "plan_key": document.plan_key,
        "expected_plan_revision": document.sha256,
        "expected_plan_id": preview.plan_id,
        "expected_execution_digest": preview.execution_digest,
        "interval_minutes": 60,
    }
    try:
        yield runtime, repository, plan, options
    finally:
        runtime.shutdown(wait_for_runs=True)
