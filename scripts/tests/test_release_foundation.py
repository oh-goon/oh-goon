from __future__ import annotations

from typing import Any
from collections.abc import Sequence

import pytest

import hashlib
import json
import subprocess
from pathlib import Path

from oh_goon.repository_tooling.release.accept_container import (
    _validated_compose_file,
    accept_container,
)
from oh_goon.repository_tooling.release.accept_release import (
    _install_locked_runtime,
    _installed_runtime_environment,
    _verify_installed_package_origin,
    _wait_http,
    accept_release,
)
from oh_goon.repository_tooling.dependency_locks import (
    ROOT as DEPENDENCY_LOCK_ROOT,
    dependency_lock_issues,
)


def _lock_tree(root: Path) -> tuple[Path, Path]:
    (root / "backend").mkdir(parents=True)
    (root / "frontend").mkdir(parents=True)
    return root / "backend/uv.lock", root / "frontend/package-lock.json"


def _write_frontend_manifest(root: Path, *, react: str = "19.2.8") -> None:
    payload = {
        "name": "oh-goon-frontend",
        "version": "1108.0.0",
        "engines": {"node": ">=22.16.0"},
        "dependencies": {"react": react},
        "devDependencies": {"typescript": "6.0.3"},
    }
    (root / "frontend/package.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_valid_locks(root: Path, *, react: str = "19.2.8") -> tuple[Path, Path]:
    uv_lock, npm_lock = _lock_tree(root)
    (root / "backend/pyproject.toml").write_text(
        '[project]\nname="oh-goon"\nversion="1.2.3"\nrequires-python=">=3.12"\n',
        encoding="utf-8",
    )
    uv_lock.write_text(
        'version = 1\nrequires-python = ">=3.12"\npackage = []\n', encoding="utf-8"
    )
    _write_frontend_manifest(root, react=react)
    npm_lock.write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {
                        "name": "oh-goon-frontend",
                        "version": "1108.0.0",
                        "engines": {"node": ">=22.16.0"},
                        "dependencies": {"react": react},
                        "devDependencies": {"typescript": "6.0.3"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return uv_lock, npm_lock


def test_dependency_lock_validator_owns_repository_root() -> None:
    assert DEPENDENCY_LOCK_ROOT == Path(__file__).resolve().parents[2]


def test_dependency_lock_check_delegates_backend_to_uv_and_frontend_to_npm(
    tmp_path: Path,
) -> None:
    _write_valid_locks(tmp_path)
    calls: list[tuple[str, ...]] = []

    def runner(argv: Sequence[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    assert dependency_lock_issues(tmp_path, runner=runner) == ()
    assert calls == [
        (
            "uv",
            "lock",
            "--project",
            str(tmp_path / "backend"),
            "--check",
        ),
        (
            "npm",
            "ci",
            "--package-lock-only",
            "--ignore-scripts",
            "--dry-run",
            "--offline",
            "--no-audit",
            "--no-fund",
        ),
    ]


def test_dependency_lock_check_detects_missing_committed_backend_lock(
    tmp_path: Path,
) -> None:
    uv_lock, _npm_lock = _write_valid_locks(tmp_path)
    uv_lock.unlink()
    issues = dependency_lock_issues(
        tmp_path,
        runner=lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="", stderr=""
        ),
    )
    assert any(
        path == uv_lock and "committed backend uv.lock" in reason
        for path, reason in issues
    )


def test_dependency_lock_check_preserves_native_npm_failure(tmp_path: Path) -> None:
    _write_valid_locks(tmp_path)

    def runner(argv: Sequence[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = tuple(argv)
        if command and command[0] == "npm":
            return subprocess.CompletedProcess(
                argv, 1, stdout="", stderr="npm ERR! package-lock mismatch"
            )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    issues = dependency_lock_issues(tmp_path, runner=runner)
    assert any(
        path == tmp_path / "frontend/package-lock.json"
        and "npm ci lock check failed: npm ERR! package-lock mismatch" in reason
        for path, reason in issues
    )


def test_locked_runtime_install_exports_uv_lock_and_installs_wheel_without_dependency_resolution(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    (root / "backend").mkdir(parents=True)
    wheel = root / "generated/release/dist/oh_goon-1108.0.0-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)
    wheel.write_bytes(b"wheel")
    temp = tmp_path / "accept"
    temp.mkdir()
    commands: list[tuple[str, ...]] = []

    def runner(
        argv: Sequence[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[object]:
        command = tuple(str(item) for item in argv)
        commands.append(command)
        if command[:2] == ("uv", "venv"):
            (temp / "venv/bin").mkdir(parents=True)
        if command[:2] == ("uv", "export"):
            output = Path(command[command.index("--output-file") + 1])
            output.write_text("fastapi==1.0 --hash=sha256:abc\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    _install_locked_runtime(root, temp=temp, wheel=wheel, runner=runner)

    export = next(command for command in commands if command[:2] == ("uv", "export"))
    dependencies = next(
        command
        for command in commands
        if command[:3] == ("uv", "pip", "install") and "-r" in command
    )
    package = next(
        command
        for command in commands
        if command[:3] == ("uv", "pip", "install") and str(wheel) in command
    )
    assert "--frozen" in export
    assert "--locked" not in export
    assert "--no-dev" in export
    assert "--no-emit-project" in export
    assert "--require-hashes" in dependencies
    assert "--no-deps" in package


def test_real_release_acceptance_delegates_dependency_admission_to_build_release(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(
        command: tuple[str, ...], **_kwargs: object
    ) -> subprocess.CompletedProcess[object]:
        calls.append(command)
        raise RuntimeError("build-release owns dependency admission")

    try:
        accept_release(tmp_path, runner=runner)
    except RuntimeError as exc:
        assert str(exc) == "build-release owns dependency admission"
    else:
        raise AssertionError("expected delegated build-release failure")
    assert calls == [("make", "build-release")]


def test_container_acceptance_fails_before_docker_without_required_source_evidence(
    tmp_path: Path,
) -> None:
    _lock_tree(tmp_path)
    issues = accept_container(tmp_path)
    assert any("backend/pyproject.toml" in issue for issue in issues)
    assert any("frontend/package.json" in issue for issue in issues)
    assert any("frontend/package-lock.json" in issue for issue in issues)


def test_release_http_wait_fails_immediately_when_installed_process_exits() -> None:
    process = subprocess.Popen(("sh", "-c", "exit 0"))
    process.wait()
    try:
        _wait_http(
            "http://127.0.0.1:9/api/health", timeout_seconds=5.0, process=process
        )
    except RuntimeError as exc:
        assert "installed release exited with code 0" in str(exc)
    else:
        raise AssertionError("expected exited installed process to fail HTTP wait")


def test_installed_runtime_environment_cannot_import_or_configure_from_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/source/backend/src")
    monkeypatch.setenv("PYTHONHOME", "/source/python")
    monkeypatch.setenv("VIRTUAL_ENV", "/source/.venv")
    monkeypatch.setenv("OH_GOON_DB", "/source/db.sqlite")
    monkeypatch.setenv("OH_GOON_CONTAINER", "1")
    monkeypatch.setenv("UV_RESOLUTION", "lowest")
    monkeypatch.setenv("UV_PRERELEASE", "always")
    monkeypatch.setenv("UV_EXCLUDE_NEWER", "2020-01-01")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/ambient/project-env")
    state = tmp_path / "state"

    env = _installed_runtime_environment(state, access_token="acceptance-token")

    assert "PYTHONPATH" not in env
    assert "PYTHONHOME" not in env
    assert "VIRTUAL_ENV" not in env
    assert "OH_GOON_CONTAINER" not in env
    assert env["OH_GOON_LOCAL_ACCESS_TOKEN"] == "acceptance-token"
    assert "UV_RESOLUTION" not in env
    assert "UV_PRERELEASE" not in env
    assert "UV_EXCLUDE_NEWER" not in env
    assert "UV_PROJECT_ENVIRONMENT" not in env
    assert env["HOME"] == str(state / "home")
    assert env["XDG_CONFIG_HOME"] == str(state / "config")
    assert env["XDG_DATA_HOME"] == str(state / "data")
    assert env["XDG_CACHE_HOME"] == str(state / "cache")
    assert env["LOCALAPPDATA"] == str(state / "local-app-data")
    assert env["OH_GOON_DB"] == str(state / "oh-goon.db")
    assert env["OH_GOON_LEAGUE_FILE"] == str(state / "league.yml")


def test_installed_release_acceptance_reuses_source_product_lifecycle_and_shared_plan_journeys() -> (
    None
):
    script = (
        Path(__file__).resolve().parents[2]
        / "backend/src/oh_goon/repository_tooling/release/accept_release.py"
    ).read_text(encoding="utf-8")
    assert "exercise_location_lifecycle(" in script
    assert (
        "exercise_shared_plan(base_url, repositories, access_token=access_token)"
        in script
    )
    assert script.index("exercise_location_lifecycle(") < script.index(
        "exercise_shared_plan(base_url, repositories, access_token=access_token)"
    )


def test_installed_release_restart_checks_location_game_tape_and_shared_plan_persistence() -> (
    None
):
    script = (
        Path(__file__).resolve().parents[2]
        / "backend/src/oh_goon/repository_tooling/release/accept_release.py"
    ).read_text(encoding="utf-8")
    assert "did not preserve location state across restart" in script
    assert "did not preserve historical Game Tape across restart" in script
    assert "did not preserve Shared Plan state across restart" in script


def test_installed_release_restart_executes_again_under_new_runtime_authority() -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "backend/src/oh_goon/repository_tooling/release/accept_release.py"
    ).read_text(encoding="utf-8")
    assert "exercise_product_runtime(" in script
    assert "post_restart_run_id = exercise_product_api(" in script
    assert script.index("exercise_product_runtime(") < script.index(
        "post_restart_run_id = exercise_product_api("
    )
    assert (
        "installed release reused historical Fleet Run identity after restart" in script
    )
    assert (
        "installed release history did not retain both pre/post-restart runs" in script
    )


def test_installed_release_acceptance_uses_configurable_serve_command() -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "backend/src/oh_goon/repository_tooling/release/accept_release.py"
    ).read_text(encoding="utf-8")
    expected = '(str(command), "serve", "--host", HOST, "--port", str(port))'
    assert script.count(expected) == 2
    assert '(str(command), "start", "--port"' not in script


def test_installed_package_origin_probe_rejects_source_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = tmp_path / "venv/bin/oh-goon"
    command.parent.mkdir(parents=True)
    python = tmp_path / "venv/bin/python"
    python.write_text("", encoding="utf-8")
    result = subprocess.CompletedProcess(
        args=(),
        returncode=0,
        stdout=f"{tmp_path}/source/oh_goon/__init__.py\n1.2.3\n",
        stderr="",
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: result)

    issues = _verify_installed_package_origin(
        command=command, temp=tmp_path, env={}, expected_version="1.2.3"
    )

    assert issues and "outside clean venv" in issues[0]


def test_installed_package_origin_probe_rejects_version_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = tmp_path / "venv/bin/oh-goon"
    command.parent.mkdir(parents=True)
    module = tmp_path / "venv/lib/python3.12/site-packages/oh_goon/__init__.py"
    module.parent.mkdir(parents=True)
    result = subprocess.CompletedProcess(
        args=(), returncode=0, stdout=f"{module}\n9.9.9\n", stderr=""
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: result)

    issues = _verify_installed_package_origin(
        command=command, temp=tmp_path, env={}, expected_version="1.2.3"
    )

    assert issues and "expected '1.2.3'" in issues[0]


def test_dependency_lock_check_does_not_parse_foreign_lock_formats_when_native_tools_accept_them(
    tmp_path: Path,
) -> None:
    uv_lock, npm_lock = _write_valid_locks(tmp_path)
    uv_lock.write_text(
        "version = 999\nanything = 'native-uv-owned'\n", encoding="utf-8"
    )
    npm_payload = json.loads(npm_lock.read_text(encoding="utf-8"))
    npm_payload["lockfileVersion"] = 999
    npm_lock.write_text(json.dumps(npm_payload), encoding="utf-8")

    issues = dependency_lock_issues(
        tmp_path,
        runner=lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="", stderr=""
        ),
    )

    assert issues == ()


def test_container_acceptance_uses_ephemeral_port_and_canonical_fleet_run_route() -> (
    None
):
    script = (
        Path(__file__).resolve().parents[2]
        / "backend/src/oh_goon/repository_tooling/release/accept_container.py"
    ).read_text(encoding="utf-8")
    assert "_allocate_acceptance_port()" in script
    assert 'port = "18787"' not in script
    assert "/api/fleet/runs/{run_id}" in script
    assert "/api/fleet-runs/{run_id}" not in script


def test_container_acceptance_uses_isolated_compose_project() -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "backend/src/oh_goon/repository_tooling/release/accept_container.py"
    ).read_text(encoding="utf-8")
    assert 'project_name = f"oh-goon-accept-' in script
    assert '"docker"' in script and '"compose"' in script
    assert '"--project-name"' in script and "project_name" in script


def test_container_acceptance_rejects_symlinked_compose_file(tmp_path: Path) -> None:
    outside = tmp_path / "outside.yml"
    outside.write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").symlink_to(outside)
    try:
        _validated_compose_file(tmp_path)
    except ValueError as exc:
        assert "regular non-symlink file" in str(exc)
    else:
        raise AssertionError("symlinked compose file was accepted")


def test_container_acceptance_pins_repository_compose_file() -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "backend/src/oh_goon/repository_tooling/release/accept_container.py"
    ).read_text(encoding="utf-8")
    assert '"--file"' in script
    assert "str(compose_file)" in script
    assert "_validated_compose_file(root)" in script


def test_container_acceptance_ignores_caller_compose_environment() -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "backend/src/oh_goon/repository_tooling/release/accept_container.py"
    ).read_text(encoding="utf-8")
    assert '("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES")' in script
    assert "env.pop(name, None)" in script
    assert "_acceptance_environment" in script


def test_release_identity_check_does_not_import_runtime_application() -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "backend/src/oh_goon/repository_tooling/release/stamp_release_version.py"
    ).read_text(encoding="utf-8")
    assert 'OPENAPI = ROOT / "generated" / "openapi" / "openapi.json"' in script
    assert "from oh_goon.api.app import create_app" not in script


def test_release_stamp_rejects_symlinked_native_manifest_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.repository_tooling.release import stamp_release_version

    version = tmp_path / "VERSION"
    version.write_text("1.2.3\n", encoding="utf-8")
    outside = tmp_path / "outside.toml"
    outside.write_text('[project]\nversion="9.9.9"\n', encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.symlink_to(outside)
    package = tmp_path / "package.json"
    package.write_text('{"version":"9.9.9"}\n', encoding="utf-8")
    package_lock = tmp_path / "package-lock.json"
    package_lock.write_text(
        '{"version":"9.9.9","packages":{"":{"version":"9.9.9"}}}\n', encoding="utf-8"
    )
    monkeypatch.setattr(stamp_release_version, "ROOT", tmp_path)
    monkeypatch.setattr(stamp_release_version, "VERSION_FILE", version)
    monkeypatch.setattr(stamp_release_version, "PYPROJECT", pyproject)
    backend_lock = tmp_path / "backend/uv.lock"
    if backend_lock.parent.is_dir() and not backend_lock.exists():
        backend_lock.write_text("version = 1\npackage = []\n", encoding="utf-8")
    monkeypatch.setattr(stamp_release_version, "BACKEND_LOCK", backend_lock)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_PACKAGE", package)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_LOCK", package_lock)

    try:
        stamp_release_version.stamp()
    except ValueError as exc:
        assert (
            "backend/pyproject.toml must be a contained regular non-symlink file"
            in str(exc)
        )
    else:
        raise AssertionError("expected symlinked release owner to be rejected")
    assert "9.9.9" in outside.read_text(encoding="utf-8")


def test_release_stamp_restores_native_owned_files_when_native_version_command_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.repository_tooling.release import stamp_release_version

    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    backend.mkdir()
    frontend.mkdir()
    version = tmp_path / "VERSION"
    version.write_text("1.2.3\n", encoding="utf-8")
    pyproject = backend / "pyproject.toml"
    pyproject.write_text('[project]\nversion="9.9.9"\n', encoding="utf-8")
    package = frontend / "package.json"
    package.write_text('{"version":"9.9.9"}\n', encoding="utf-8")
    package_lock = frontend / "package-lock.json"
    package_lock.write_text(
        '{"version":"9.9.9","packages":{"":{"version":"9.9.9"}}}\n', encoding="utf-8"
    )
    monkeypatch.setattr(stamp_release_version, "ROOT", tmp_path)
    monkeypatch.setattr(stamp_release_version, "VERSION_FILE", version)
    monkeypatch.setattr(stamp_release_version, "PYPROJECT", pyproject)
    backend_lock = (
        backend / "uv.lock"
        if backend.exists() and backend.is_dir()
        else tmp_path / "backend/uv.lock"
    )
    if backend_lock.parent.is_dir() and not backend_lock.exists():
        backend_lock.write_text("version = 1\npackage = []\n", encoding="utf-8")
    monkeypatch.setattr(stamp_release_version, "BACKEND_LOCK", backend_lock)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_PACKAGE", package)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_LOCK", package_lock)
    originals = {
        path: path.read_bytes()
        for path in (pyproject, backend_lock, package, package_lock)
    }

    def fake_run(command: tuple[str, ...], *, cwd: Path) -> str:
        if command[:2] == ("uv", "version"):
            pyproject.write_text('[project]\nversion="1.2.3"\n', encoding="utf-8")
            return "1.2.3"
        raise RuntimeError("simulated npm version failure")

    monkeypatch.setattr(stamp_release_version, "_run", fake_run)
    try:
        stamp_release_version.stamp()
    except RuntimeError as exc:
        assert "simulated npm version failure" in str(exc)
    else:
        raise AssertionError("expected native stamp failure")
    assert {path: path.read_bytes() for path in originals} == originals


def test_make_exposes_explicit_backend_resolution_and_frontend_lock_authoring_targets() -> (
    None
):
    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text(
        encoding="utf-8"
    )
    sync = makefile.split("sync:", 1)[1].split("\n\n", 1)[0]
    frontend = makefile.split("frontend-lock:", 1)[1].split("\n\n", 1)[0]

    assert "uv lock --project backend" in sync
    assert "$(DEPENDENCY_POLICY) public-lock-check" in sync
    assert "Native uv authors backend/uv.lock" in sync
    assert "npm install --package-lock-only" in frontend
    assert "npm ci --package-lock-only" in frontend
    assert "DEPENDENCY_LOCK_AUTHORING" not in makefile
    assert "UV_REPOSITORY_TOOLING" not in makefile
    assert "dependency_lock_authoring.py" not in makefile


def test_release_set_version_delegates_backend_and_frontend_versioning_to_uv_and_npm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.repository_tooling.release import stamp_release_version

    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    backend.mkdir()
    frontend.mkdir()
    version = tmp_path / "VERSION"
    version.write_text("1.2.3\n", encoding="utf-8")
    pyproject = backend / "pyproject.toml"
    pyproject.write_text('[project]\nversion="1.2.3"\n', encoding="utf-8")
    backend_lock = backend / "uv.lock"
    backend_lock.write_text("version = 1\npackage = []\n", encoding="utf-8")
    package = frontend / "package.json"
    package.write_text('{"version":"1.2.3"}\n', encoding="utf-8")
    package_lock = frontend / "package-lock.json"
    package_lock.write_text(
        '{"version":"1.2.3","packages":{"":{"version":"1.2.3"}}}\n', encoding="utf-8"
    )
    openapi = tmp_path / "generated/openapi/openapi.json"

    monkeypatch.setattr(stamp_release_version, "ROOT", tmp_path)
    monkeypatch.setattr(stamp_release_version, "VERSION_FILE", version)
    monkeypatch.setattr(stamp_release_version, "PYPROJECT", pyproject)
    monkeypatch.setattr(stamp_release_version, "BACKEND_LOCK", backend_lock)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_PACKAGE", package)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_LOCK", package_lock)
    monkeypatch.setattr(stamp_release_version, "OPENAPI", openapi)
    monkeypatch.setattr(
        stamp_release_version, "GENERATED_FRONTEND", tmp_path / "generated/frontend"
    )
    monkeypatch.setattr(
        stamp_release_version,
        "GOON_TOOL_AUTHORITY",
        backend / "src/oh_goon/canonical_goon_tool_authority.json",
    )
    monkeypatch.setattr(
        stamp_release_version, "assert_canonical_source_layout", lambda _root: None
    )

    incoming = stamp_release_version.ReleaseDependencyReceipt(
        backend_lock_sha256=hashlib.sha256(backend_lock.read_bytes()).hexdigest(),
        frontend_lock_sha256=hashlib.sha256(package_lock.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        stamp_release_version,
        "_assert_release_authoring_prerequisites",
        lambda: incoming,
    )

    def post_receipt(
        receipt: stamp_release_version.ReleaseDependencyReceipt,
    ) -> stamp_release_version.ReleaseDependencyReceipt:
        assert receipt is incoming
        assert (
            hashlib.sha256(backend_lock.read_bytes()).hexdigest()
            == incoming.backend_lock_sha256
        )
        return stamp_release_version.ReleaseDependencyReceipt(
            backend_lock_sha256=incoming.backend_lock_sha256,
            frontend_lock_sha256=hashlib.sha256(package_lock.read_bytes()).hexdigest(),
        )

    monkeypatch.setattr(
        stamp_release_version, "_post_authoring_dependency_receipt", post_receipt
    )

    def regenerate() -> None:
        openapi.parent.mkdir(parents=True, exist_ok=True)
        openapi.write_text('{"info":{"version":"2.0.0"}}\n', encoding="utf-8")

    monkeypatch.setattr(
        stamp_release_version, "_regenerate_generated_authority", regenerate
    )
    calls: list[tuple[str, ...]] = []

    def fake_run(command: tuple[str, ...], *, cwd: Path) -> str:
        calls.append(command)
        if command == ("uv", "version", "--frozen", "2.0.0"):
            pyproject.write_text('[project]\nversion="2.0.0"\n', encoding="utf-8")
            return "2.0.0"
        if command == ("uv", "version", "--short"):
            return "2.0.0"
        if command == (
            "npm",
            "version",
            "2.0.0",
            "--no-git-tag-version",
            "--allow-same-version",
        ):
            package.write_text('{"version":"2.0.0"}\n', encoding="utf-8")
            package_lock.write_text(
                '{"version":"2.0.0","packages":{"":{"version":"2.0.0"}}}\n',
                encoding="utf-8",
            )
            return "v2.0.0"
        if command == ("npm", "pkg", "get", "version"):
            return '"2.0.0"'
        raise AssertionError(f"unexpected native version command: {command!r}")

    monkeypatch.setattr(stamp_release_version, "_run", fake_run)
    stamp_release_version.set_version("2.0.0")

    assert version.read_text(encoding="utf-8") == "2.0.0\n"
    assert (
        hashlib.sha256(backend_lock.read_bytes()).hexdigest()
        == incoming.backend_lock_sha256
    )
    assert calls == [
        ("uv", "version", "--frozen", "2.0.0"),
        ("npm", "version", "2.0.0", "--no-git-tag-version", "--allow-same-version"),
        ("uv", "version", "--short"),
        ("npm", "pkg", "get", "version"),
    ]


def test_release_source_cleanup_removes_only_declared_transients(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.repository_tooling import source_normalization

    (tmp_path / "backend/src/pkg").mkdir(parents=True)
    authored = tmp_path / "backend/src/pkg/source.py"
    authored.write_text("pass\n", encoding="utf-8")
    cache = tmp_path / "backend/src/pkg/__pycache__"
    cache.mkdir()
    (cache / "source.cpython-312.pyc").write_bytes(b"cache")
    nested_pytest_cache = tmp_path / "backend/.pytest_cache/v/cache"
    nested_pytest_cache.mkdir(parents=True)
    (nested_pytest_cache / "nodeids").write_text("[]", encoding="utf-8")
    (tmp_path / "frontend/node_modules/pkg").mkdir(parents=True)
    (tmp_path / "frontend/node_modules/pkg/index.js").write_text("x", encoding="utf-8")
    browser_root = tmp_path / "frontend/.oh-goon-playwright-browsers"
    (browser_root / "chromium").mkdir(parents=True)
    (browser_root / "chromium/browser").write_text("x", encoding="utf-8")
    preserved = tmp_path / "preserved-cleanup-target"
    preserved.mkdir()
    preserved_file = preserved / "owned-elsewhere"
    preserved_file.write_text("keep", encoding="utf-8")
    (browser_root / "chromium/external-link").symlink_to(
        preserved, target_is_directory=True
    )
    preserved.chmod(0o555)
    preserved_file.chmod(0o444)
    preserved_mode = preserved.stat().st_mode
    preserved_file_mode = preserved_file.stat().st_mode
    for directory in (
        browser_root / "chromium",
        browser_root,
        tmp_path / "frontend/node_modules/pkg",
        tmp_path / "frontend/node_modules",
    ):
        directory.chmod(directory.stat().st_mode & ~0o222)
    (tmp_path / "coverage-python.json").write_text("{}", encoding="utf-8")
    zone = tmp_path / "backend/src/pkg/source.py:Zone.Identifier"
    zone.write_text("[ZoneTransfer]\nZoneId=3\n", encoding="utf-8")
    zone_data = tmp_path / "README.md:Zone.Identifier:$DATA"
    zone_data.write_text("[ZoneTransfer]\n", encoding="utf-8")
    ordinary_colon = tmp_path / "backend/src/pkg/source.py:review"
    ordinary_colon.write_text("authored\n", encoding="utf-8")
    report_names = (
        "coverage",
        "playwright-report",
        "playwright-runtime-report",
        "test-results",
    )
    for name in report_names:
        output = tmp_path / "frontend" / name / "result.txt"
        output.parent.mkdir(parents=True)
        output.write_text("generated\n", encoding="utf-8")
        authored_report = tmp_path / "docs" / name / "analysis.md"
        authored_report.parent.mkdir(parents=True)
        authored_report.write_text("authored\n", encoding="utf-8")
    monkeypatch.setattr(source_normalization, "ROOT", tmp_path)

    removed = source_normalization.clean()

    assert authored.is_file()
    assert ordinary_colon.is_file()
    assert not zone.exists()
    assert not zone_data.exists()
    assert "backend/src/pkg/source.py:Zone.Identifier" in removed
    assert "README.md:Zone.Identifier:$DATA" in removed
    assert not cache.exists()
    assert not (tmp_path / "backend/.pytest_cache").exists()
    assert not (tmp_path / "frontend/node_modules").exists()
    assert not (tmp_path / "frontend/.oh-goon-playwright-browsers").exists()
    assert preserved_file.read_text(encoding="utf-8") == "keep"
    assert preserved.stat().st_mode == preserved_mode
    assert preserved_file.stat().st_mode == preserved_file_mode
    # Restore test-owned fixture permissions so temporary-directory teardown remains portable.
    preserved.chmod(preserved.stat().st_mode | 0o200)
    preserved_file.chmod(preserved_file.stat().st_mode | 0o200)
    assert not (tmp_path / "coverage-python.json").exists()
    assert "frontend/node_modules" in removed
    assert "frontend/.oh-goon-playwright-browsers" in removed
    assert not (tmp_path / "generated/release/static").exists()
    for name in report_names:
        assert f"frontend/{name}" in removed
        assert not (tmp_path / "frontend" / name).exists()
        assert (tmp_path / "docs" / name / "analysis.md").read_text() == "authored\n"


def test_release_source_cleanup_removes_generated_static_without_recreating_bootstrap_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.repository_tooling import source_normalization

    static = tmp_path / "generated/release/static"
    static.mkdir(parents=True)
    (static / "index.html").write_text("stale", encoding="utf-8")
    monkeypatch.setattr(source_normalization, "ROOT", tmp_path)

    removed = source_normalization.clean()

    assert "generated/release/static" in removed
    assert not static.exists()


def test_source_normalization_never_mutates_git_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.repository_tooling import source_normalization

    git_cache = tmp_path / ".git/hooks/__pycache__"
    git_cache.mkdir(parents=True)
    git_file = git_cache / "hook.cpython-312.pyc"
    git_file.write_bytes(b"git-owned")
    source_cache = tmp_path / "pkg/__pycache__"
    source_cache.mkdir(parents=True)
    (source_cache / "module.cpython-312.pyc").write_bytes(b"transient")
    monkeypatch.setattr(source_normalization, "ROOT", tmp_path)

    source_normalization.clean()

    assert git_file.read_bytes() == b"git-owned"
    assert not source_cache.exists()


def test_package_source_checkpoint_help_does_not_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from oh_goon.repository_tooling.release import package_source_checkpoint

    monkeypatch.setattr(
        package_source_checkpoint, "OUTPUT_DIR", tmp_path / "release-artifacts"
    )
    try:
        package_source_checkpoint.main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("argparse --help should exit without package production")
    assert not (tmp_path / "release-artifacts").exists()
    assert "canonical retained source package" in capsys.readouterr().out


def test_release_stamp_updates_certification_source_version_transactionally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.repository_tooling.release import stamp_release_version

    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    certification = tmp_path / "docs" / "certification" / "questions"
    backend.mkdir()
    frontend.mkdir()
    certification.mkdir(parents=True)
    version = tmp_path / "VERSION"
    version.write_text("2.0.0\n", encoding="utf-8")
    pyproject = backend / "pyproject.toml"
    pyproject.write_text('[project]\nversion="1.0.0"\n', encoding="utf-8")
    package = frontend / "package.json"
    package.write_text('{"version":"1.0.0"}\n', encoding="utf-8")
    package_lock = frontend / "package-lock.json"
    package_lock.write_text(
        '{"version":"1.0.0","packages":{"":{"version":"1.0.0"}}}\n', encoding="utf-8"
    )
    metadata = certification / "question-metadata.yml"
    metadata.write_text("schema_version: 2\nsource_version: 1.0.0\n", encoding="utf-8")
    monkeypatch.setattr(stamp_release_version, "ROOT", tmp_path)
    monkeypatch.setattr(stamp_release_version, "VERSION_FILE", version)
    monkeypatch.setattr(stamp_release_version, "PYPROJECT", pyproject)
    backend_lock = (
        backend / "uv.lock"
        if backend.exists() and backend.is_dir()
        else tmp_path / "backend/uv.lock"
    )
    if backend_lock.parent.is_dir() and not backend_lock.exists():
        backend_lock.write_text("version = 1\npackage = []\n", encoding="utf-8")
    monkeypatch.setattr(stamp_release_version, "BACKEND_LOCK", backend_lock)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_PACKAGE", package)
    monkeypatch.setattr(stamp_release_version, "FRONTEND_LOCK", package_lock)

    def fake_run(command: tuple[str, ...], *, cwd: Path) -> str:
        if command[:2] == ("uv", "version"):
            pyproject.write_text('[project]\nversion="2.0.0"\n', encoding="utf-8")
        elif command[:2] == ("npm", "version"):
            package.write_text('{"version":"2.0.0"}\n', encoding="utf-8")
            package_lock.write_text(
                '{"version":"2.0.0","packages":{"":{"version":"2.0.0"}}}\n',
                encoding="utf-8",
            )
        return ""

    monkeypatch.setattr(stamp_release_version, "_run", fake_run)
    stamp_release_version.stamp()
    assert (
        metadata.read_text(encoding="utf-8")
        == "schema_version: 2\nsource_version: 2.0.0\n"
    )
