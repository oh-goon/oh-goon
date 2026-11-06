from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

import pytest

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
from oh_goon.repository_tooling.architecture.common import Violation


def _write(root: Path, relative: str, source: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def _messages(violations: Iterable[Violation]) -> tuple[str, ...]:
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
        "        return identity.observe_directory(verify=True)\n",
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
        "# subprocess.run and subprocess.Popen are forbidden here.\ndef verify():\n    return 0\n",
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


def test_certification_frontdoor_comment_is_not_process_authority(
    tmp_path: Path,
) -> None:
    _certification_makefile(tmp_path)
    _write(
        tmp_path,
        "backend/src/oh_goon/services/certification_frontdoor.py",
        "# subprocess.Popen is owned elsewhere.\ndef main():\n    return 0\n",
    )
    assert _check_certification_preexecution_authority(tmp_path) == []


def test_certification_frontdoor_aliased_process_authority_is_rejected(
    tmp_path: Path,
) -> None:
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


def test_strict_step_comments_are_not_ambient_or_process_authority(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "backend/src/oh_goon/integrations/_fleet_plan_step_local_execution.py",
        "# os.environ, os.getenv(), and subprocess.run are deliberately not used.\n"
        "def execute(prepared):\n    return prepared\n",
    )
    assert _check_execution_authority_ii(tmp_path) == []


def test_strict_step_aliases_cannot_hide_ambient_or_process_authority(
    tmp_path: Path,
) -> None:
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
        "from time import sleep as pause\ndef place():\n    pause(0.1)\n",
    )
    assert any(
        "never raw time polling" in message
        for message in _messages(_check_scheduler_backpressure_authority(tmp_path))
    )


def test_hashlib_comment_and_hashmarks_import_alias_are_representation_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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


def test_renamed_hashlib_owner_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        for message in _messages(
            filesystem_identity_contract.check_filesystem_identity_contract()
        )
    )


def test_m120_private_helper_name_in_comment_is_not_release_authority(
    tmp_path: Path,
) -> None:
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


def test_workflow_private_domain_words_in_comments_are_not_authority(
    tmp_path: Path,
) -> None:
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


def test_workflow_renamed_database_control_state_owner_is_rejected(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "backend/src/oh_goon/workflow.py",
        "import sqlite3 as local_store\n" + _workflow_source(),
    )
    assert any(
        "local authority module sqlite3" in message
        for message in _messages(check_workflow_runner_boundary(tmp_path))
    )


def test_callable_defaults_and_observed_callers_do_not_prove_every_call_target(
    tmp_path: Path,
) -> None:
    from oh_goon.repository_tooling.architecture.common import python_semantic_facts

    owner = _write(
        tmp_path,
        "owner.py",
        "from subprocess import Popen\n"
        "def invoke(factory=Popen):\n    return factory(['echo', 'test'])\n"
        "invoke(Popen)\n",
    )
    facts = python_semantic_facts(owner, imported_only=True)
    assert not any(name == "subprocess.Popen" for name, _call in facts.calls)
    native = facts.evidence.packet()["facts"]
    assert any(
        fact["kind"] == "default-binding" and fact["qualification"] == "conditional"
        for fact in native
    )
    assert any(
        fact["kind"] == "call-site"
        and fact["qualification"] == "unknown"
        and "parameter-value-unknown" in fact["reasons"]
        for fact in native
    )
    owner.write_text("from subprocess import Popen\nPopen(['echo', 'test'])\n")
    direct = python_semantic_facts(owner, imported_only=True)
    assert [name for name, _call in direct.calls] == ["subprocess.Popen"]


def test_unavailable_producer_cannot_activate_local_python_binding_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.repository_tooling.architecture import common

    owner = _write(
        tmp_path, "owner.py", "from subprocess import Popen\nPopen(['echo'])\n"
    )
    baseline = common.python_semantic_facts(owner, imported_only=True)
    assert [name for name, _call in baseline.calls] == ["subprocess.Popen"]
    unavailable = baseline.evidence.packet()
    unavailable.update(
        availability="unavailable",
        completeness="unknown",
        truncation="unknown",
        facts=[],
        reason="source-binding-provider-failed",
    )
    refused = replace(
        baseline.evidence,
        canonical_packet_json=json.dumps(unavailable),
        source_bytes=None,
    )
    monkeypatch.setattr(
        common,
        "observe_source_bindings",
        lambda _root, paths, **_options: {paths[0]: refused},
    )

    with pytest.raises(ValueError, match="source-binding-provider-failed"):
        common.python_semantic_facts(owner, imported_only=True)


def test_producer_bindings_cannot_attach_to_different_captured_python_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.repository_tooling.architecture import common

    owner = _write(
        tmp_path, "owner.py", "from subprocess import Popen\nPopen(['echo'])\n"
    )
    baseline = common.python_semantic_facts(owner, imported_only=True)
    assert [name for name, _call in baseline.calls] == ["subprocess.Popen"]
    # Keep the call range identical: range agreement cannot bind changed bytes.
    owner.write_text("from subprocess import Popen\nprobe(['echo'])\n")
    monkeypatch.setattr(
        common,
        "observe_source_bindings",
        lambda _root, paths, **_options: {paths[0]: baseline.evidence},
    )

    with pytest.raises(ValueError, match="differ from captured Python structure"):
        common.python_semantic_facts(owner, imported_only=True)
