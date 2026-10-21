from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture._structural_project_dependency_contract import (
    _check_lock_authority,
)

LOCK_CHECK = "backend/src/oh_goon/repository_tooling/dependency_locks.py"


def _write_lock_check(
    root: Path, *, backend: str, frontend: str, extra: str = ""
) -> Path:
    path = root / LOCK_CHECK
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "from pathlib import Path\n"
        "\n"
        "def _native_check(command, *, cwd, runner, label):\n"
        "    return None\n"
        "\n"
        "def backend_dependency_lock_issues(root, *, runner=None):\n"
        f"{backend}\n"
        "\n"
        "def frontend_dependency_lock_issues(root, *, runner=None):\n"
        f"{frontend}\n"
        f"{extra}",
        encoding="utf-8",
    )
    (root / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    return path


def _equivalent_backend() -> str:
    return (
        "    verification_flags = ('--check', '--project')\n"
        "    backend_native = ['uv', 'lock', *verification_flags, str(root / 'backend')]\n"
        "    return _native_check(backend_native, cwd=root, runner=runner, label='backend')"
    )


def _equivalent_frontend() -> str:
    return (
        "    npm_flags = ['--dry-run', '--package-lock-only']\n"
        "    frontend_native = ('npm', 'ci', *npm_flags, '--offline')\n"
        "    return _native_check(frontend_native, cwd=root / 'frontend', runner=runner, label='frontend')"
    )


def test_lock_architecture_accepts_equivalent_native_source_spelling(
    tmp_path: Path,
) -> None:
    _write_lock_check(
        tmp_path,
        backend=_equivalent_backend(),
        frontend=_equivalent_frontend(),
    )

    assert _check_lock_authority(tmp_path) == []


def test_lock_architecture_rejects_presence_only_uv_authority(tmp_path: Path) -> None:
    _write_lock_check(
        tmp_path,
        backend=(
            "    command = ('uv', 'lock', '--project', str(root / 'backend'), '--check-exists')\n"
            "    return _native_check(command, cwd=root, runner=runner, label='backend')"
        ),
        frontend=_equivalent_frontend(),
    )

    violations = _check_lock_authority(tmp_path)

    assert any("native uv lock --check" in item.message for item in violations)


def test_lock_architecture_rejects_non_ci_npm_authority(tmp_path: Path) -> None:
    _write_lock_check(
        tmp_path,
        backend=_equivalent_backend(),
        frontend=(
            "    command = ('npm', 'install', '--dry-run', '--package-lock-only')\n"
            "    return _native_check(command, cwd=root / 'frontend', runner=runner, label='frontend')"
        ),
    )

    violations = _check_lock_authority(tmp_path)

    assert any("native npm ci" in item.message for item in violations)


def test_lock_architecture_rejects_parallel_lock_content_interpretation(
    tmp_path: Path,
) -> None:
    path = _write_lock_check(
        tmp_path,
        backend=(
            _equivalent_backend()
            + "\n    payload = (root / 'backend' / 'uv.lock').read_text(encoding='utf-8')\n"
            "    return payload"
        ),
        frontend=_equivalent_frontend(),
    )

    violations = _check_lock_authority(tmp_path)

    assert any(
        item.path == path and "must not read or parse lock contents" in item.message
        for item in violations
    )
