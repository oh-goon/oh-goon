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
        "[project]\nname = \"oh-goon\"\nversion = \"0.0.0\"\n"
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
        .replace(' = "oh_goon.repository_tooling.placeholder:main"', "='oh_goon.repository_tooling.placeholder:main'")
        .replace("oh-goon-workflow='oh_goon.repository_tooling.placeholder:main'", "oh-goon-workflow = 'oh_goon.repository_tooling.placeholder:main'"),
        encoding="utf-8",
    )

    messages = _messages(root)
    assert not any("missing canonical pyproject entrypoint" in message for message in messages)


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
        'def whatever_private_name() -> int:\n    return 0\n\n'
        'if __name__ == "__main__":\n    raise SystemExit(whatever_private_name())\n',
        encoding="utf-8",
    )
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(":cli'", ":whatever_private_name'"),
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
    assert any("pre-commit repository Python must use uv" in message for message in messages)
    assert any("frontend repository commands must execute through npm scripts" in message for message in messages)


def test_project_python_contract_matches_make_targets_by_exact_name(tmp_path: Path) -> None:
    root = _minimal_repo(tmp_path)
    _write(
        root,
        "Makefile",
        "diagnostic-system-check:\n\t@true\n\n"
        "certification-setup:\n\t@true\n",
    )
    messages = _messages(root)
    assert not any("obsolete developer bootstrap target must not exist: setup" in message for message in messages)


def test_project_python_contract_requires_direct_native_frontend_materialization(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(root, "Makefile", "frontend-install:\n\tsh scripts/frontend_install.sh\n")
    _write(root, "scripts/frontend_install.sh", "#!/bin/sh\nnpm ci\n")
    messages = _messages(root)
    assert any("frontend materialization must delegate directly to native npm ci" in message for message in messages)


def test_project_python_contract_requires_path_safe_native_command_and_run_log_presentation(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(
        root,
        "backend/src/oh_goon/repository_tooling/native_command.py",
        'child_env = os.environ.copy()\n_native_uv_environment_evidence(child_env)\n'
        '"UV_DEFAULT_INDEX", "UV_INDEX"\n"UV_CONFIG_FILE"\n',
    )
    _write(root, "scripts/run_logged.sh", '#!/bin/sh\n"$@"\n')
    messages = _messages(root)
    assert any("--path-label privacy protocol" in message for message in messages)
    assert any("observable privacy/progress protocol" in message for message in messages)


def test_project_python_contract_requires_live_certification_progress_projection(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(root, "backend/src/oh_goon/cli_runtime_plans.py", "game_tape_live_output = True\n")
    assert any(
        "certification progress must remain live presentation" in message
        for message in _messages(root)
    )


def _write_certification_progress_relationship(
    root: Path,
    *,
    projection_name: str = "project_transient_output",
    run_method_name: str = "observe_transient_output",
) -> None:
    _write(
        root,
        "backend/src/oh_goon/application.py",
        "class ApplicationRuntime:\n"
        "    runs: FleetRunControlPlane\n",
    )
    _write(
        root,
        "backend/src/oh_goon/services/live_projection.py",
        "class GameTapeLiveOutputBatch:\n"
        "    pass\n\n"
        "class FleetRunDetail:\n"
        "    game_tape = object()\n\n"
        "def _select_tape(detail: FleetRunDetail):\n"
        "    return detail.game_tape\n\n"
        f"def {projection_name}(store, detail: FleetRunDetail, *, after: int) -> GameTapeLiveOutputBatch:\n"
        "    _ = _select_tape(detail)\n"
        "    return store.snapshot(after)\n",
    )
    _write(
        root,
        "backend/src/oh_goon/services/fleet_run_control_plane.py",
        f"from oh_goon.services.live_projection import {projection_name} as projection\n\n"
        "class GameTapeLiveOutputBatch:\n"
        "    pass\n\n"
        "def _delegate(owner, run_id: str, *, after: int) -> GameTapeLiveOutputBatch:\n"
        "    return projection(owner._live_output, owner.get(run_id), after=after)\n\n"
        "class FleetRunControlPlane:\n"
        f"    def {run_method_name}(self, run_id: str, *, after: int = 0) -> GameTapeLiveOutputBatch:\n"
        "        return _delegate(self, run_id, after=after)\n",
    )
    _write(
        root,
        "backend/src/oh_goon/cli_runtime_plans.py",
        "class ApplicationRuntime:\n"
        "    pass\n\n"
        "def _read_progress(runtime: ApplicationRuntime):\n"
        f"    return runtime.runs.{run_method_name}('run-1', after=0)\n\n"
        "def render(runtime: ApplicationRuntime):\n"
        "    batch = _read_progress(runtime)\n"
        "    print('Observed activity: Goon 1/1')\n"
        "    print('Certification working')\n"
        "    print('OH_GOON_PROGRESS_FD')\n"
        "    return batch\n",
    )


def test_certification_progress_ownership_is_identifier_representation_invariant(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write_certification_progress_relationship(
        root,
        projection_name="derive_ephemeral_chunks",
        run_method_name="stream_ephemeral_chunks",
    )

    assert not any(
        "certification progress must" in message
        for message in _messages(root)
    )


def test_certification_progress_dead_identifier_decoys_cannot_authorize_relationship(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(
        root,
        "backend/src/oh_goon/cli_runtime_plans.py",
        "# game_tape_live_output is only a dead compatibility comment\n"
        "DEAD = ('game_tape_live_output', 'Observed activity: Goon', "
        "'Certification working', 'OH_GOON_PROGRESS_FD')\n",
    )

    assert any(
        "ApplicationRuntime.runs Run/Game Tape service owner" in message
        for message in _messages(root)
    )


def test_certification_progress_requires_real_game_tape_projection_owner(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write_certification_progress_relationship(root)
    _write(
        root,
        "backend/src/oh_goon/services/live_projection.py",
        "class GameTapeLiveOutputBatch:\n"
        "    pass\n\n"
        "class FleetRunDetail:\n"
        "    pass\n\n"
        "def _dead_owner_decoy(detail):\n"
        "    return detail.game_tape\n\n"
        "def project_transient_output(store, detail: FleetRunDetail, *, after: int) -> GameTapeLiveOutputBatch:\n"
        "    return store.snapshot(after)\n"
        "# game_tape_live_output\n",
    )

    assert any(
        "ApplicationRuntime.runs Run/Game Tape service owner" in message
        for message in _messages(root)
    )


def test_project_python_contract_requires_native_lock_authority_and_privacy(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(root, "backend/src/oh_goon/repository_tooling/dependency_lock_authoring.py", "VALUE = 1\n")
    _write(root, "backend/src/oh_goon/repository_tooling/dependency_locks.py", "import tomllib\n")
    _write(root, "backend/src/oh_goon/repository_tooling/public_dependency_privacy.py", "VALUE = 1\n")
    messages = _messages(root)
    assert any("dependency lock authoring must be owned directly by uv/npm" in message for message in messages)
    assert any("native uv lock --check" in message for message in messages)
    assert any("native npm ci" in message for message in messages)
    assert any("must not read or parse lock contents" in message for message in messages)
    assert any(
        "dependency privacy" in message and ("must consume" in message or "consumer is missing" in message)
        for message in messages
    )


def test_project_python_contract_rejects_presence_only_uv_readiness(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(
        root,
        "backend/src/oh_goon/repository_tooling/dependency_locks.py",
        "def _native_check(command, *, cwd, runner, label):\n"
        "    return None\n"
        "\n"
        "def backend_dependency_lock_issues(root, *, runner=None):\n"
        "    command = ('uv', 'lock', '--project', str(root / 'backend'), '--check-exists')\n"
        "    return _native_check(command, cwd=root, runner=runner, label='backend')\n"
        "\n"
        "def frontend_dependency_lock_issues(root, *, runner=None):\n"
        "    command = ('npm', 'ci', '--package-lock-only', '--dry-run')\n"
        "    return _native_check(command, cwd=root, runner=runner, label='frontend')\n",
    )
    messages = _messages(root)
    assert any("native uv lock --check" in message for message in messages)


def test_project_python_contract_rejects_parallel_browser_lock_selection(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(
        root,
        "backend/src/oh_goon/repository_tooling/release/browser_acceptance_container.py",
        "import json\n"
        "from pathlib import Path\n"
        "authority = Path('frontend/package-lock.json')\n"
        "payload = json.loads(authority.read_text())\n",
    )
    messages = _messages(root)
    assert any("must not parse package-lock semantics in parallel with npm" in message for message in messages)


def test_project_python_contract_requires_operator_uv_native_command_boundary(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(
        root,
        "backend/src/oh_goon/repository_tooling/native_command.py",
        'DEFAULT_MAX_ATTEMPTS = 20\n'
        'child_env = os.environ.copy()\n'
        'child_env.pop("UV_SYSTEM_CERTS", None)\n',
    )
    messages = _messages(root)
    assert any(
        "native-tool observer must own exactly one non-repeating native execution attempt" in message
        for message in messages
    )
    assert any("certification must inherit operator/native environment instead of removing UV_SYSTEM_CERTS" in message for message in messages)


def test_native_command_structural_contract_rejects_renamed_retry_loop_and_aliased_sleep(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(
        root,
        "backend/src/oh_goon/repository_tooling/native_command.py",
        "import argparse\n"
        "import subprocess\n"
        "import time as clock\n\n"
        "pause = clock.sleep\n\n"
        "def observe(command, *, path_labels=()):\n"
        "    for turn in range(3):\n"
        "        result = subprocess.run(command, check=False)\n"
        "        if result.returncode == 0:\n"
        "            return 0\n"
        "        pause(0.01)\n"
        "    return result.returncode\n\n"
        "def parser():\n"
        "    result = argparse.ArgumentParser()\n"
        "    result.add_argument('--path-label', action='append', default=[])\n"
        "    return result\n\n"
        "def main(argv=None):\n"
        "    args = parser().parse_args(argv)\n"
        "    labels = tuple(args.path_label)\n"
        "    return observe(('uv', 'sync'), path_labels=labels)\n",
    )

    assert any(
        "native-tool observer must own exactly one non-repeating native execution attempt" in message
        for message in _messages(root)
    )


def test_native_command_structural_contract_rejects_recursive_retry_under_renamed_owner(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(
        root,
        "backend/src/oh_goon/repository_tooling/native_command.py",
        "import argparse\n"
        "import subprocess\n\n"
        "def launch(command, *, path_labels=()):\n"
        "    result = subprocess.run(command, check=False)\n"
        "    if result.returncode:\n"
        "        return launch(command, path_labels=path_labels)\n"
        "    return result.returncode\n\n"
        "def parser():\n"
        "    result = argparse.ArgumentParser()\n"
        "    result.add_argument('--path-label', action='append', default=[])\n"
        "    return result\n\n"
        "def main(argv=None):\n"
        "    args = parser().parse_args(argv)\n"
        "    labels = tuple(args.path_label)\n"
        "    return launch(('uv', 'sync'), path_labels=labels)\n",
    )

    assert any(
        "native-tool observer must own exactly one non-repeating native execution attempt" in message
        for message in _messages(root)
    )


def test_native_command_structural_contract_accepts_one_attempt_without_retry_name_sentinels(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(
        root,
        "backend/src/oh_goon/repository_tooling/native_command.py",
        "import argparse\n"
        "import subprocess\n\n"
        "DEFAULT_MAX_ATTEMPTS = 'documentation only, not execution authority'\n"
        "RETRY_DELAYS_SECONDS = 'also inert'\n"
        "DEAD_SLEEP_TEXT = 'time.sleep is not execution authority'\n\n"
        "def renamed_observer(command, *, path_labels=()):\n"
        "    return subprocess.run(command, check=False).returncode\n\n"
        "def parser():\n"
        "    result = argparse.ArgumentParser()\n"
        "    result.add_argument('--path-label', action='append', default=[])\n"
        "    return result\n\n"
        "def main(argv=None):\n"
        "    args = parser().parse_args(argv)\n"
        "    labels = tuple(args.path_label)\n"
        "    return renamed_observer(('uv', 'sync'), path_labels=labels)\n",
    )

    assert not any(
        "native-tool observer must own exactly one non-repeating native execution attempt" in message
        for message in _messages(root)
    )


def test_project_python_contract_requires_native_release_stamping(
    tmp_path: Path,
) -> None:
    root = _minimal_repo(tmp_path)
    _write(
        root,
        "backend/src/oh_goon/repository_tooling/release/stamp_release_version.py",
        "import tomllib\n"
        "from pathlib import Path\n"
        "BACKEND_LOCK = Path('backend/uv.lock')\n"
        "backend_lock_text = BACKEND_LOCK.read_text()\n"
        "tomllib.loads(backend_lock_text)\n",
    )
    messages = _messages(root)
    assert any("release version stamping must delegate to native package-manager command" in message for message in messages)
    assert any("must not manually reinterpret package-manager lock metadata" in message for message in messages)


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
