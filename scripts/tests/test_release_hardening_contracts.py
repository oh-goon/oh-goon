from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

import pytest

from oh_goon.repository_tooling.release import (
    accept_container as accept_container_module,
)
from oh_goon.repository_tooling.release import accept_release as accept_release_module
from oh_goon.repository_tooling.release.accept_container import _validated_dockerfile
from oh_goon.repository_tooling.dependency_locks import dependency_lock_issues
from oh_goon.repository_tooling import startup_environment


def test_release_build_runs_repository_check_before_wheel_build() -> None:
    root = Path(__file__).resolve().parents[2]
    script = (root / "scripts/release/build_release.sh").read_text(encoding="utf-8")
    assert "make check" in script
    assert script.index("make check") < script.index("uv build --wheel")


def test_release_build_does_not_gate_on_startup_diagnostics() -> None:
    root = Path(__file__).resolve().parents[2]
    script = (root / "scripts/release/build_release.sh").read_text(encoding="utf-8")
    assert "oh-goon-startup-environment check" not in script


def test_no_build_isolation_backend_is_in_locked_build_group() -> None:
    root = Path(__file__).resolve().parents[2]
    payload = tomllib.loads(
        (root / "backend/pyproject.toml").read_text(encoding="utf-8")
    )
    build_requires = set(payload["build-system"]["requires"])
    build = set(payload["dependency-groups"]["build"])
    assert "hatchling>=1.32.0" in build_requires
    assert "editables>=0.5" in build_requires
    assert "editables>=0.5" in build
    assert "hatchling>=1.32.0" in build
    script = (root / "scripts/release/build_release.sh").read_text(encoding="utf-8")
    assert "uv build --wheel --no-build-isolation --no-sources" in script


def test_source_sync_uses_declared_build_requirements_and_release_stays_locked() -> (
    None
):
    root = Path(__file__).resolve().parents[2]
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    payload = tomllib.loads(
        (root / "backend/pyproject.toml").read_text(encoding="utf-8")
    )
    release = (root / "scripts/release/build_release.sh").read_text(encoding="utf-8")
    init = "init:" + makefile.split("\ninit:", 1)[1].split("\n\n", 1)[0]
    assert set(payload["build-system"]["requires"]) == {
        "editables>=0.5",
        "hatchling>=1.32.0",
    }
    assert "\nsetup:" not in makefile
    assert "\ndeveloper-dependencies-acquire:" not in makefile
    assert "certification-setup:" not in makefile
    assert "certification-dependencies-prepare:" not in makefile
    assert (
        "uv sync --project backend --locked --group dev --group quality --group build"
        in init
    )
    assert "npm ci --prefer-offline --ignore-scripts --no-audit --no-fund" in init
    assert "certification-dependencies-" not in init
    assert "make release-locks-check" in release
    assert "uv lock --project backend --check" not in release
    assert "npm ci --package-lock-only" not in release
    assert "uv build --wheel --no-build-isolation --no-sources" in release
    assert "oh-goon-python-install" not in release
    assert "uv sync --group dev" not in release
    assert "--no-install-project" not in release


def test_release_build_keeps_test_groups_in_its_native_materialization(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    script = tmp_path / "scripts/release/build_release.sh"
    script.parent.mkdir(parents=True)
    script.write_bytes((root / "scripts/release/build_release.sh").read_bytes())
    (tmp_path / "backend").mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    arguments_path = tmp_path / "uv-arguments"
    for name, body in {
        "make": "#!/bin/sh\nexit 0\n",
        "uv": '#!/bin/sh\nprintf "%s\\n" "$@" > "$RELEASE_UV_ARGUMENTS"\nexit 73\n',
    }.items():
        executable = tools / name
        executable.write_text(body, encoding="utf-8")
        executable.chmod(0o755)

    result = subprocess.run(
        ["bash", str(script)],
        env={
            **os.environ,
            "PATH": str(tools) + os.pathsep + os.defpath,
            "RELEASE_UV_ARGUMENTS": str(arguments_path),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 73
    arguments = arguments_path.read_text(encoding="utf-8").splitlines()
    assert arguments[0] == "sync" and "--locked" in arguments
    groups = {
        arguments[index + 1]
        for index, argument in enumerate(arguments[:-1])
        if argument == "--group"
    }
    assert {"dev", "quality", "build"} <= groups
    assert "--no-dev" not in arguments


def test_release_acceptance_has_one_lock_admissibility_owner_before_materialization() -> (
    None
):
    root = Path(__file__).resolve().parents[2]
    accept_release = (
        root / "backend/src/oh_goon/repository_tooling/release/accept_release.py"
    ).read_text(encoding="utf-8")
    release = (root / "scripts/release/build_release.sh").read_text(encoding="utf-8")
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    lock_target = (
        "release-locks-check:"
        + makefile.split("release-locks-check:", 1)[1].split("\n\n", 1)[0]
    )

    assert "dependency_lock_issues" not in accept_release
    assert release.count("make release-locks-check") == 1
    assert "uv lock --project backend --check" not in release
    assert "npm ci --package-lock-only" not in release
    assert "backend-lock-check frontend-lock-check" in lock_target


def test_container_acceptance_dockerfile_must_stay_under_repository_docker_tree(
    tmp_path: Path,
) -> None:
    (tmp_path / "docker/ubuntu").mkdir(parents=True)
    valid = tmp_path / "docker/ubuntu/Dockerfile"
    valid.write_text("FROM scratch\n", encoding="utf-8")
    assert (
        _validated_dockerfile(tmp_path, "docker/ubuntu/Dockerfile")
        == "docker/ubuntu/Dockerfile"
    )
    outside = tmp_path / "outside.Dockerfile"
    outside.write_text("FROM scratch\n", encoding="utf-8")
    try:
        _validated_dockerfile(tmp_path, "outside.Dockerfile")
    except ValueError as exc:
        assert "must remain under docker/" in str(exc)
    else:
        raise AssertionError("expected external Dockerfile to be rejected")


def test_lock_authoring_has_no_python_transaction_wrapper() -> None:
    root = Path(__file__).resolve().parents[2]
    assert not (
        root / "backend/src/oh_goon/repository_tooling/dependency_lock_authoring.py"
    ).exists()
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    assert "backend-lock:" not in makefile


def test_vendor_containers_install_built_wheel_instead_of_source_pythonpath() -> None:
    root = Path(__file__).resolve().parents[2]
    for vendor in ("ubuntu", "opensuse"):
        text = (root / "docker" / vendor / "Dockerfile").read_text(encoding="utf-8")
        assert "uv build --wheel --no-build-isolation --no-sources" in text
        assert "uv sync --locked --project backend" in text
        assert "COPY vendor vendor" not in text
        assert "uv pip install --python backend/.venv/bin/python --no-deps" in text
        assert (
            "COPY --from=frontend-build /src/frontend/dist generated/release/static"
            in text
        )
        assert "PYTHONPATH=/opt/oh-goon/backend/src" not in text


def test_container_acceptance_proves_installed_wheel_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "VERSION").write_text("1114.0.0\n", encoding="utf-8")
    captured: list[tuple[str, ...]] = []

    def run(
        argv: tuple[str, ...], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout="/opt/venv/site-packages/oh_goon/__init__.py\n1114.0.0\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", run)
    issues = accept_container_module._verify_container_package_origin(
        ("docker", "compose"), root=tmp_path, env={}
    )

    assert issues == ()
    assert captured and captured[0] == (
        "docker",
        "compose",
        "exec",
        "-T",
        "oh-goon",
        "oh-goon-runtime-identity",
    )


def test_vendor_containers_verify_wheel_before_runtime_install() -> None:
    root = Path(__file__).resolve().parents[2]
    for vendor in ("ubuntu", "opensuse"):
        text = (root / "docker" / vendor / "Dockerfile").read_text(encoding="utf-8")
        build_index = text.index("uv build --wheel --no-build-isolation")
        verify_index = text.index("oh-goon-release-artifact-check")
        install_index = text.index(
            "uv pip install --python backend/.venv/bin/python --no-deps"
        )
        assert build_index < verify_index < install_index
        assert "generated/release/dist/oh_goon-*-py3-none-any.whl" in text


def test_vendor_wheel_builder_copies_release_identity_for_verification() -> None:
    root = Path(__file__).resolve().parents[2]
    for vendor in ("ubuntu", "opensuse"):
        text = (root / "docker" / vendor / "Dockerfile").read_text(encoding="utf-8")
        assert text.index("COPY VERSION VERSION") < text.index(
            "oh-goon-release-artifact-check"
        )


def test_native_lock_validation_rejects_parent_symlink_escape_before_package_manager_call(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "backend").symlink_to(outside, target_is_directory=True)
    frontend = root / "frontend"
    frontend.mkdir()
    (frontend / "package-lock.json").write_text("{}\n", encoding="utf-8")

    issues = dependency_lock_issues(root)
    assert any(
        "contained regular non-symlink file" in reason for _path, reason in issues
    )


def test_dependency_lock_checker_rejects_parent_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "uv.lock").write_text("version = 1\npackage = []\n", encoding="utf-8")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "backend").symlink_to(outside, target_is_directory=True)
    frontend = root / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
    (frontend / "package-lock.json").write_text(
        '{"lockfileVersion":3,"packages":{"":{"name":"x"}}}\n', encoding="utf-8"
    )

    issues = dependency_lock_issues(root)
    assert any(
        "contained regular non-symlink file" in reason for _path, reason in issues
    )


def test_release_stamp_rejects_parent_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.repository_tooling.release import stamp_release_version

    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (root / "backend").symlink_to(outside, target_is_directory=True)
    (outside / "pyproject.toml").write_text(
        '[project]\nversion="9.9.9"\n', encoding="utf-8"
    )
    frontend = root / "frontend"
    frontend.mkdir()
    package = frontend / "package.json"
    package.write_text('{"version":"9.9.9"}\n', encoding="utf-8")
    monkeypatch.setattr(stamp_release_version, "ROOT", root)
    monkeypatch.setattr(stamp_release_version, "VERSION_FILE", root / "VERSION")
    monkeypatch.setattr(
        stamp_release_version, "PYPROJECT", root / "backend/pyproject.toml"
    )
    monkeypatch.setattr(stamp_release_version, "BACKEND_LOCK", root / "backend/uv.lock")
    monkeypatch.setattr(stamp_release_version, "FRONTEND_PACKAGE", package)

    try:
        stamp_release_version.stamp()
    except ValueError as exc:
        assert "must remain inside the checkout" in str(exc)
    else:
        raise AssertionError("parent symlink escape was accepted")
    assert "9.9.9" in (outside / "pyproject.toml").read_text(encoding="utf-8")


def test_container_node_builders_satisfy_frontend_manifest_requirement() -> None:
    import json
    import re

    root = Path(__file__).resolve().parents[2]
    package = json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))
    minimum = tuple(
        int(part) for part in package["engines"]["node"].removeprefix(">=").split(".")
    )
    for vendor in ("ubuntu", "opensuse"):
        text = (root / "docker" / vendor / "Dockerfile").read_text(encoding="utf-8")
        match = re.search(r"FROM node:(\d+(?:\.\d+){2})-bookworm-slim", text)
        assert match is not None
        actual = tuple(int(part) for part in match.group(1).split("."))
        assert actual >= minimum
        assert "FROM ghcr.io/astral-sh/uv:" in text


def test_release_acceptance_reverifies_wheel_after_browser_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "VERSION").write_text("1114.0.0\n", encoding="utf-8")
    events: list[object] = []

    def runner(
        command: tuple[str, ...], **_kwargs: object
    ) -> subprocess.CompletedProcess[object]:
        events.append(command)
        if command == ("make", "build-release"):
            wheel = (
                tmp_path / "generated/release/dist/oh_goon-1114.0.0-py3-none-any.whl"
            )
            wheel.parent.mkdir(parents=True)
            wheel.write_bytes(b"wheel")
        return subprocess.CompletedProcess(command, 0)

    def verify(_root: Path) -> tuple[str, ...]:
        events.append("verified-wheel")
        return ()

    def install(_root: Path, *, temp: Path, wheel: Path, runner: object) -> Path:
        del wheel, runner
        events.append("installed-wheel")
        return temp / "venv"

    def accept_runtime(
        _root: Path, _command: Path, _temp: Path, *, port: int
    ) -> tuple[str, ...]:
        del port
        events.append("accepted-runtime")
        return ()

    def forbidden_startup_diagnostic(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("release acceptance must not gate on startup diagnostics")

    monkeypatch.setattr(
        startup_environment, "startup_report", forbidden_startup_diagnostic
    )
    monkeypatch.setattr(accept_release_module, "release_artifact_issues", verify)
    monkeypatch.setattr(accept_release_module, "_install_locked_runtime", install)
    monkeypatch.setattr(
        accept_release_module, "_accept_installed_runtime", accept_runtime
    )

    assert accept_release_module.accept_release(tmp_path, runner=runner) == ()
    assert events.index(("npm", "run", "browser:release")) < events.index(
        "verified-wheel"
    )
    assert events.index("verified-wheel") < events.index("installed-wheel")
    assert events.index("installed-wheel") < events.index("accepted-runtime")


def test_container_acceptance_reaches_native_docker_without_startup_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "docker/ubuntu").mkdir(parents=True)
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "docker/ubuntu/Dockerfile").write_text(
        "FROM scratch\n", encoding="utf-8"
    )
    (tmp_path / "backend/pyproject.toml").write_text(
        "[project]\nname='x'\n", encoding="utf-8"
    )
    (tmp_path / "backend/uv.lock").write_text("version = 1\n", encoding="utf-8")
    (tmp_path / "frontend/package.json").write_text('{"name":"x"}\n', encoding="utf-8")
    (tmp_path / "frontend/package-lock.json").write_text(
        '{"lockfileVersion":3,"packages":{}}\n', encoding="utf-8"
    )
    calls: list[tuple[str, ...]] = []

    def native_run(command: tuple[str, ...], **_kwargs: object) -> object:
        calls.append(tuple(command))
        raise RuntimeError("native docker operation reached")

    monkeypatch.setattr(subprocess, "run", native_run)
    with pytest.raises(RuntimeError, match="native docker operation reached"):
        accept_container_module.accept_container(tmp_path)
    assert calls
    assert calls[0][:2] == ("docker", "compose")
    assert calls[0][-2:] == ("build", "oh-goon")
    assert all("version" not in command for command in calls)


def test_source_product_acceptance_isolates_ambient_runtime_environment() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (
        root / "backend/src/oh_goon/repository_tooling/acceptance/product.py"
    ).read_text(encoding="utf-8")
    assert "_source_runtime_environment" in text
    assert 'name.startswith("OH_GOON_")' in text
    assert '"HOME": str(state / "home")' in text
    assert '"XDG_CONFIG_HOME": str(state / "config")' in text
    assert '"PYTHONPATH": source' not in text
    for name in (
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "UV_RESOLUTION",
        "UV_PRERELEASE",
        "UV_EXCLUDE_NEWER",
    ):
        assert f'"{name}"' in text
    assert '"--no-dev"' in text


def test_container_runtime_consumes_current_public_lock_then_installs_hash_pinned_runtime() -> (
    None
):
    root = Path(__file__).resolve().parents[2]
    for vendor in ("ubuntu", "opensuse"):
        text = (root / "docker" / vendor / "Dockerfile").read_text(encoding="utf-8")
        assert "ghcr.io/astral-sh/uv:0.12.5" in text
        assert (
            "uv sync --locked --project backend --group dev --group quality --group build --no-install-project --no-python-downloads"
            in text
        )
        assert (
            "uv export --project backend --frozen --no-dev --no-emit-project --format requirements.txt"
            in text
        )
        assert (
            "uv pip install --python backend/.venv/bin/python --require-hashes" in text
        )
        assert "COPY backend/pyproject.toml backend/uv.lock backend/" in text
        assert "COPY vendor vendor" not in text
        assert "uv build --wheel --no-build-isolation --no-sources" in text
