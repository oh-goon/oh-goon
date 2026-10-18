from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture import filesystem_identity_contract
from oh_goon.repository_tooling.architecture._structural_authority_certification import (
    _check_certification_preexecution_authority,
    _check_execution_authority_ii,
    _check_scheduler_backpressure_authority,
    _check_self_verification_accelerator,
)
from oh_goon.repository_tooling.architecture._structural_authority_workflow import (
    check_workflow_runner_boundary,
)
from oh_goon.repository_tooling.architecture._structural_project_dependency_contract import (
    _check_native_authority_deduplication,
)
from oh_goon.repository_tooling.architecture.common import clear_python_ast_cache


def _write(root: Path, relative: str, source: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    clear_python_ast_cache()
    return path


def _messages(violations) -> tuple[str, ...]:
    return tuple(item.message for item in violations)


def _certification_makefile(root: Path) -> None:
    _write(
        root,
        "Makefile",
        "goon-static-admission:\n\t@true\n"
        "certify:\n\t@true\n"
        "prepare-release:\n\t@true\n"
        "certification-state-reset:\n\t@true\n",
    )


def _valid_hashmarks_adapter(root: Path) -> Path:
    return _write(
        root,
        "identity.py",
        "import hashmarks as hm\n"
        "def identify(path):\n"
        "    if path.is_file():\n"
        "        return hm.hash_file(path)\n"
        "    with hm.RepositoryIdentity(path) as identity:\n"
        "        return identity.snapshot('*', verify=True)\n",
    )


def _workflow_source(extra: str = "") -> str:
    return (
        "def _backend_cli(args, *, env):\n"
        "    raise NotImplementedError\n"
        "def prepare(env):\n"
        "    command = ['dev', 'bootstrap-certification', '--state-root', '/tmp/state']\n"
        "    return _backend_cli(command, env=env)\n"
        f"{extra}"
    )


def test_subprocess_comments_are_not_self_cli_authority(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "backend/src/oh_goon/cli_self.py",
        "# subprocess.run and subprocess.Popen are forbidden here.\n"
        "def verify():\n    return 0\n",
    )
    assert _check_self_verification_accelerator(tmp_path) == []


def test_aliased_subprocess_call_is_still_self_cli_authority(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "backend/src/oh_goon/cli_self.py",
        "from subprocess import run as launch\n"
        "def verify():\n    return launch(['echo', 'wrong'])\n",
    )
    assert any(
        "not launch child commands directly" in message
        for message in _messages(_check_self_verification_accelerator(tmp_path))
    )


def test_certification_frontdoor_comment_is_not_process_authority(tmp_path: Path) -> None:
    _certification_makefile(tmp_path)
    _write(
        tmp_path,
        "backend/src/oh_goon/services/certification_frontdoor.py",
        "# subprocess.Popen is owned elsewhere.\n"
        "def main():\n    return 0\n",
    )
    assert _check_certification_preexecution_authority(tmp_path) == []


def test_certification_frontdoor_aliased_process_authority_is_rejected(tmp_path: Path) -> None:
    _certification_makefile(tmp_path)
    _write(
        tmp_path,
        "backend/src/oh_goon/services/certification_frontdoor.py",
        "from subprocess import Popen as start_child\n"
        "def main():\n    return start_child(['echo', 'wrong'])\n",
    )
    assert any(
        "second subprocess path" in message
        for message in _messages(_check_certification_preexecution_authority(tmp_path))
    )


def test_strict_step_comments_are_not_ambient_or_process_authority(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "backend/src/oh_goon/integrations/_fleet_plan_step_local_execution.py",
        "# os.environ, os.getenv(), and subprocess.run are deliberately not used.\n"
        "def execute(prepared):\n    return prepared\n",
    )
    assert _check_execution_authority_ii(tmp_path) == []


def test_strict_step_aliases_cannot_hide_ambient_or_process_authority(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "backend/src/oh_goon/integrations/_fleet_plan_step_local_execution.py",
        "from os import environ as ambient\n"
        "from subprocess import run as launch\n"
        "def execute():\n"
        "    copied = ambient.copy()\n"
        "    return launch(['echo', 'wrong'], env=copied)\n",
    )
    messages = _messages(_check_execution_authority_ii(tmp_path))
    assert any("never ambient process environment" in message for message in messages)
    assert any("delegate process lifecycle" in message for message in messages)


def test_placement_time_comments_are_not_polling_authority(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "backend/src/oh_goon/runtime/_executor_placement.py",
        "# time.sleep and time.monotonic are raw polling APIs and are not used.\n"
        "def place(clock):\n    return clock.now()\n",
    )
    assert _check_scheduler_backpressure_authority(tmp_path) == []


def test_placement_aliased_sleep_is_still_raw_polling_authority(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "backend/src/oh_goon/runtime/_executor_placement.py",
        "from time import sleep as pause\n"
        "def place():\n    pause(0.1)\n",
    )
    assert any(
        "never raw time polling" in message
        for message in _messages(_check_scheduler_backpressure_authority(tmp_path))
    )


def test_hashlib_comment_and_hashmarks_import_alias_are_representation_only(
    tmp_path: Path, monkeypatch
) -> None:
    adapter = _valid_hashmarks_adapter(tmp_path)
    identity = _write(
        tmp_path,
        "content_snapshot.py",
        "# import hashlib; hashlib.sha256 is not an identity owner here.\n"
        "def snapshot():\n    return None\n",
    )
    monkeypatch.setattr(filesystem_identity_contract, "ADAPTER", adapter)
    monkeypatch.setattr(filesystem_identity_contract, "IDENTITY_MODULES", (identity,))
    assert filesystem_identity_contract.check_filesystem_identity_contract() == []


def test_renamed_hashlib_owner_is_rejected(tmp_path: Path, monkeypatch) -> None:
    adapter = _valid_hashmarks_adapter(tmp_path)
    identity = _write(
        tmp_path,
        "content_snapshot.py",
        "import hashlib as digest_library\n"
        "fingerprint = digest_library.new\n"
        "def snapshot(payload):\n    return fingerprint('sha256', payload).hexdigest()\n",
    )
    monkeypatch.setattr(filesystem_identity_contract, "ADAPTER", adapter)
    monkeypatch.setattr(filesystem_identity_contract, "IDENTITY_MODULES", (identity,))
    assert any(
        "delegate to Hashmarks" in message
        for message in _messages(filesystem_identity_contract.check_filesystem_identity_contract())
    )


def test_m120_private_helper_name_in_comment_is_not_release_authority(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "backend/src/oh_goon/repository_tooling/release/accept_release.py",
        "# dependency_lock_issues was a historical private helper name.\n"
        "def accept():\n    return ()\n",
    )
    assert _check_native_authority_deduplication(tmp_path) == []


def test_renamed_dependency_lock_owner_import_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "backend/src/oh_goon/repository_tooling/release/accept_release.py",
        "from oh_goon.repository_tooling.dependency_locks import frontend_dependency_lock_issues as inspect_native_state\n"
        "def accept(root):\n    return inspect_native_state(root)\n",
    )
    assert any(
        "delegate dependency admissibility" in message
        for message in _messages(_check_native_authority_deduplication(tmp_path))
    )


def test_renamed_local_package_lock_parser_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "backend/src/oh_goon/repository_tooling/release/accept_release.py",
        "import json as document_codec\n"
        "from pathlib import Path\n"
        "def accept(root):\n"
        "    lock_path = Path(root) / 'frontend' / 'package-lock.json'\n"
        "    raw_document = lock_path.read_text(encoding='utf-8')\n"
        "    model = document_codec.loads(raw_document)\n"
        "    return model.get('packages', {})\n",
    )
    assert any(
        "re-probing or reinterpreting" in message
        for message in _messages(_check_native_authority_deduplication(tmp_path))
    )


def test_exact_lock_bytes_may_be_transported_without_semantic_redecision(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "backend/src/oh_goon/repository_tooling/release/accept_release.py",
        "from pathlib import Path\n"
        "def evidence(root):\n"
        "    lock_path = Path(root) / 'frontend' / 'package-lock.json'\n"
        "    return lock_path.read_bytes()\n",
    )
    assert _check_native_authority_deduplication(tmp_path) == []


def test_workflow_private_domain_words_in_comments_are_not_authority(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "backend/src/oh_goon/workflow.py",
        _workflow_source(
            "# DATABASE_SCHEMA_VERSION FleetRunOutcome repository_id( systemd-run Delegate=yes\n"
        ),
    )
    assert check_workflow_runner_boundary(tmp_path) == []


def test_workflow_renamed_local_identity_helper_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "backend/src/oh_goon/workflow.py",
        "import hashlib as digest_library\n"
        + _workflow_source(
            "fingerprint = digest_library.sha256\n"
            "def local_subject(payload):\n"
            "    return fingerprint(payload).hexdigest()\n"
        ),
    )
    assert any(
        "identity/resource semantics" in message
        for message in _messages(check_workflow_runner_boundary(tmp_path))
    )


def test_workflow_renamed_database_control_state_owner_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "backend/src/oh_goon/workflow.py",
        "import sqlite3 as local_store\n" + _workflow_source(),
    )
    assert any(
        "local authority module sqlite3" in message
        for message in _messages(check_workflow_runner_boundary(tmp_path))
    )
