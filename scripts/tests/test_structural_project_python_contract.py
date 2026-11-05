from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture.structural_project_python_contract import (
    _check_project_python_execution_ownership,
)

ROOT = Path(__file__).resolve().parents[2]

_REQUIRED_ENTRYPOINTS = (
    "oh-goon-workflow",
    "oh-goon-architecture",
    "oh-goon-release-version",
    "oh-goon-package-source",
    "oh-goon-local-state",
    "oh-goon-native-command",
    "oh-goon-source-layout",
    "oh-goon-container",
)


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _minimal_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    scripts = "\n".join(
        f'{name} = "oh_goon.repository_tooling.placeholder:main"'
        for name in _REQUIRED_ENTRYPOINTS
    )
    _write(
        root,
        "backend/pyproject.toml",
        '[project]\nname = "oh-goon"\nversion = "0.0.0"\n'
        f"[project.scripts]\n{scripts}\n",
    )
    return root


def _messages(root: Path) -> list[str]:
    return [
        violation.message
        for violation in _check_project_python_execution_ownership(root)
    ]


def test_current_repository_satisfies_project_python_contract() -> None:
    assert _check_project_python_execution_ownership(ROOT) == []


def test_required_entrypoint_ownership_is_toml_representation_invariant(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    pyproject = root / "backend/pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8")
        .replace(
            ' = "oh_goon.repository_tooling.placeholder:main"',
            "='oh_goon.repository_tooling.placeholder:main'",
        )
        .replace(
            "oh-goon-workflow='oh_goon.repository_tooling.placeholder:main'",
            "oh-goon-workflow = 'oh_goon.repository_tooling.placeholder:main'",
        ),
        encoding="utf-8",
    )

    messages = _messages(root)
    assert not any(
        "missing canonical pyproject entrypoint" in message for message in messages
    )


def test_project_python_contract_requires_every_canonical_entrypoint(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    pyproject = root / "backend/pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            'oh-goon-architecture = "oh_goon.repository_tooling.placeholder:main"\n',
            "",
        ),
        encoding="utf-8",
    )
    assert any(
        "missing canonical pyproject entrypoint: oh-goon-architecture" in message
        for message in _messages(root)
    )


def test_project_python_contract_rejects_script_python_owner(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(root, "scripts/legacy_owner.py", "VALUE = 1\n")
    assert any(
        "repository Python implementations must live under" in message
        for message in _messages(root)
    )


def test_project_python_contract_rejects_direct_python_and_shebang_owners(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(root, "Makefile", "legacy:\n\tpython3 -S scripts/tool.py\n")
    _write(
        root,
        "backend/src/oh_goon/repository_tooling/ghost.py",
        "#!/usr/bin/env python3\nVALUE = 1\n",
    )
    messages = _messages(root)
    assert any("direct python/python3 execution" in message for message in messages)
    assert any("direct Python shebang" in message for message in messages)


def test_project_python_contract_requires_registered_repeated_python_commands(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(
        root,
        "backend/src/oh_goon/repository_tooling/ghost_command.py",
        'def main() -> int:\n    return 0\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n',
    )
    assert any(
        "repeated repository Python command must be a named" in message
        for message in _messages(root)
    )


def test_project_python_contract_command_ownership_is_callable_name_invariant(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    module = _write(
        root,
        "backend/src/oh_goon/repository_tooling/ghost_command.py",
        'def cli() -> int:\n    return 0\n\nif __name__ == "__main__":\n    raise SystemExit(cli())\n',
    )

    assert any(
        "repeated repository Python command must be a named" in message
        for message in _messages(root)
    )

    pyproject = root / "backend/pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8")
        + "tool = 'oh_goon.repository_tooling.ghost_command:cli'\n",
        encoding="utf-8",
    )
    assert not any(
        violation.path == module
        and "repeated repository Python command must be a named" in violation.message
        for violation in _check_project_python_execution_ownership(root)
    )

    module.write_text(
        "def whatever_private_name() -> int:\n    return 0\n\n"
        'if __name__ == "__main__":\n    raise SystemExit(whatever_private_name())\n',
        encoding="utf-8",
    )
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            ":cli'", ":whatever_private_name'"
        ),
        encoding="utf-8",
    )
    assert not any(
        violation.path == module
        and "repeated repository Python command must be a named" in violation.message
        for violation in _check_project_python_execution_ownership(root)
    )

    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            "tool = 'oh_goon.repository_tooling.ghost_command:whatever_private_name'\n",
            "",
        ),
        encoding="utf-8",
    )
    assert any(
        violation.path == module
        and "repeated repository Python command must be a named" in violation.message
        for violation in _check_project_python_execution_ownership(root)
    )


def test_project_python_contract_requires_precommit_and_make_tool_ownership(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(
        root,
        ".pre-commit-config.yaml",
        "repos:\n- repo: local\n  hooks:\n  - id: bad\n"
        "    entry: bash -lc 'PYTHONPATH=backend/src python3 -m oh_goon'\n",
    )
    _write(root, "Makefile", "bad:\n\tnpx playwright test\n")
    messages = _messages(root)
    assert any(
        "pre-commit repository Python must use uv" in message for message in messages
    )
    assert any(
        "frontend repository commands must execute through npm scripts" in message
        for message in messages
    )


def test_project_python_contract_matches_make_targets_by_exact_name(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(
        root,
        "Makefile",
        "diagnostic-system-check:\n\t@true\n\ncertification-setup:\n\t@true\n",
    )
    messages = _messages(root)
    assert not any(
        "obsolete developer bootstrap target must not exist: setup" in message
        for message in messages
    )


def test_project_python_contract_requires_direct_native_frontend_materialization(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(root, "Makefile", "frontend-install:\n\tsh scripts/frontend_install.sh\n")
    _write(root, "scripts/frontend_install.sh", "#!/bin/sh\nnpm ci\n")
    messages = _messages(root)
    assert any(
        "frontend materialization must delegate directly to native npm ci" in message
        for message in messages
    )


def test_project_python_contract_requires_path_safe_native_command_and_run_log_presentation(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(
        root,
        "backend/src/oh_goon/repository_tooling/native_command.py",
        "child_env = os.environ.copy()\n_native_uv_environment_evidence(child_env)\n"
        '"UV_DEFAULT_INDEX", "UV_INDEX"\n"UV_CONFIG_FILE"\n',
    )
    _write(root, "scripts/run_logged.sh", '#!/bin/sh\n"$@"\n')
    messages = _messages(root)
    assert any("--path-label privacy protocol" in message for message in messages)
    assert any(
        "observable privacy/progress protocol" in message for message in messages
    )


def test_project_python_contract_requires_live_certification_progress_projection(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(
        root,
        "backend/src/oh_goon/cli_runtime_plans.py",
        "game_tape_live_output = True\n",
    )
    assert any(
        "certification progress must remain live presentation" in message
        for message in _messages(root)
    )


def test_project_python_contract_requires_native_lock_authority_and_privacy(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(
        root,
        "backend/src/oh_goon/repository_tooling/dependency_lock_authoring.py",
        "VALUE = 1\n",
    )
    _write(
        root,
        "backend/src/oh_goon/repository_tooling/dependency_locks.py",
        "import tomllib\n",
    )
    _write(
        root,
        "backend/src/oh_goon/repository_tooling/public_dependency_privacy.py",
        "VALUE = 1\n",
    )
    messages = _messages(root)
    assert any(
        "dependency lock authoring must be owned directly by uv/npm" in message
        for message in messages
    )
    assert any(
        "must not read or parse lock contents" in message for message in messages
    )
    assert any(
        "dependency privacy" in message
        and ("must consume" in message or "consumer is missing" in message)
        for message in messages
    )


def test_project_python_contract_requires_operator_uv_native_command_boundary(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(
        root,
        "backend/src/oh_goon/repository_tooling/native_command.py",
        "DEFAULT_MAX_ATTEMPTS = 20\n"
        "child_env = os.environ.copy()\n"
        'child_env.pop("UV_SYSTEM_CERTS", None)\n',
    )
    messages = _messages(root)
    assert any(
        "certification must inherit operator/native environment instead of removing UV_SYSTEM_CERTS"
        in message
        for message in messages
    )


def test_native_command_public_path_label_declaration_remains_required(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    native = _write(
        root,
        "backend/src/oh_goon/repository_tooling/native_command.py",
        "def parser(root):\n"
        "    root.add_argument('--path-label', action='append', default=[])\n",
    )
    assert not any(
        "--path-label privacy protocol" in message for message in _messages(root)
    )

    native.write_text("def parser(root):\n    return root\n", encoding="utf-8")

    assert any(
        "--path-label privacy protocol" in message for message in _messages(root)
    )


def test_project_python_contract_rejects_network_capable_shipped_goons(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(
        root,
        "templates/goons/oh-goon/example/goon.yml",
        "version: 1\nnetwork: host_unrestricted\n",
    )
    assert any(
        "must not acquire host-unrestricted egress implicitly" in message
        for message in _messages(root)
    )
