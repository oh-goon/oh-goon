"""T0B2B2A: locally observed OS facts must never claim authority or custody."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from tests.factories.x0_architecture import replace_python_syntax

from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_native_custody_observer_non_authority,
)
from oh_goon.repository_tooling.custody_host_observation import (
    _proc_start_time,
    _public_key_fingerprint,
    observe_native_custody,
)

SUBJECT = "admission_issuer_evidence/source_identity"


def _repo(root: Path) -> None:
    (root / "backend").mkdir(parents=True)
    shutil.copyfile(
        ROOT / "backend/trust-authorities.json", root / "backend/trust-authorities.json"
    )
    shutil.copyfile(
        ROOT / "backend/trust-custody-evidence.json", root / "backend/trust-custody-evidence.json"
    )


def _fixture(tmp_path: Path, pid: int = 123) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    _repo(root)
    proc = tmp_path / "proc"
    directory = proc / str(pid)
    (directory / "ns").mkdir(parents=True)
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text("12345678-1234-1234-1234-123456789abc")
    # Field 3 starts after the closing ')' and field 22 is tail position 19.
    (directory / "stat").write_text(
        f"{pid} (a trick (name)) S " + " ".join(["0"] * 18 + ["5678"] + ["0"] * 25)
    )
    (directory / "status").write_text(
        "Name:\tobs\nUid:\t1000\t1000\t1000\t1000\nGid:\t1001\t1001\t1001\t1001\n"
    )
    for kind in ("user", "mnt", "pid", "net"):
        (directory / "ns" / kind).symlink_to(f"{kind}:[40123]")
    return root, proc


def test_readonly_observation_is_not_custody_or_gate_a(tmp_path):
    root, proc = _fixture(tmp_path)
    sample = observe_native_custody(root=root, subject=SUBJECT, pid=123, proc_root=proc)
    assert sample["pid_starttime_ticks"] == 5678
    assert sample["principals"]["uid"] == [1000] * 4
    assert sample["namespace_links"]["net"] == "net:[40123]"
    assert sample["custody_proven"] is False
    assert sample["independent_custodian_proven"] is False
    assert sample["gate_a"] == "BLOCKED"
    assert sample["effect_permission"] == "NONE" and sample["runtime_authority"] is False
    assert sample["public_key"] is None
    assert (
        sample["policy_sha256"]
        == json.loads((root / "backend/trust-custody-evidence.json").read_text())["policy_sha256"]
    )


@pytest.mark.parametrize(
    "attack",
    [
        "unknown-subject",
        "policy-swap",
        "symlink-pid",
        "stale-stat",
        "bad-uid",
        "missing-net",
        "bad-boot",
        "invalid-pid",
    ],
)
def test_missing_or_substituted_native_facts_fail_closed(tmp_path, attack):
    root, proc = _fixture(tmp_path)
    pid = 123
    if attack == "unknown-subject":
        subject = "unknown"
    else:
        subject = SUBJECT
    if attack == "policy-swap":
        p = root / "backend/trust-authorities.json"
        p.write_text(p.read_text() + "\n")
    elif attack == "symlink-pid":
        shutil.rmtree(proc / "123")
        (proc / "123").symlink_to(tmp_path)
    elif attack == "stale-stat":
        (proc / "123" / "stat").write_text("123 malformed stat")
    elif attack == "bad-uid":
        (proc / "123" / "status").write_text("Uid:\t0\nGid:\t0\n")
    elif attack == "missing-net":
        (proc / "123" / "ns" / "net").unlink()
    elif attack == "bad-boot":
        (proc / "sys/kernel/random/boot_id").write_text("not a boot id")
    elif attack == "invalid-pid":
        pid = -1
    with pytest.raises((OSError, ValueError)):
        observe_native_custody(root=root, subject=subject, pid=pid, proc_root=proc)


def test_public_key_only_and_no_private_key_bytes(tmp_path):
    secret = Ed25519PrivateKey.generate()
    public = tmp_path / "review.pub"
    public.write_bytes(secret.public_key().public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH))
    fingerprint = _public_key_fingerprint(public)
    assert len(fingerprint["ed25519_public_key_sha256"]) == 64
    assert set(fingerprint) == {"ed25519_public_key_sha256", "file_uid", "file_gid", "file_mode"}
    symlink = tmp_path / "link.pub"
    symlink.symlink_to(public)
    with pytest.raises((OSError, ValueError)):
        _public_key_fingerprint(symlink)
    from cryptography.hazmat.primitives.serialization import NoEncryption, PrivateFormat

    private = tmp_path / "private.pem"
    private.write_bytes(secret.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    with pytest.raises((OSError, ValueError)):
        _public_key_fingerprint(private)


def test_stat_comm_parenthesis_handled():
    assert _proc_start_time(b"12 (tricky (name)) S " + b"0 " * 18 + b"99 0") == 99


def test_x0_non_authoritative_observer_and_mutants(tmp_path):
    assert _check_native_custody_observer_non_authority(ROOT) == []
    (tmp_path / "backend/src/oh_goon/repository_tooling").mkdir(parents=True)
    (tmp_path / "backend/src/oh_goon/runtime").mkdir(parents=True)
    (tmp_path / "backend").mkdir(exist_ok=True)
    shutil.copy(ROOT / "backend/pyproject.toml", tmp_path / "backend/pyproject.toml")
    source = ROOT / "backend/src/oh_goon/repository_tooling/custody_host_observation.py"
    target = tmp_path / "backend/src/oh_goon/repository_tooling/custody_host_observation.py"
    raw = source.read_text()
    for mutation in (
        replace_python_syntax(raw, "'custody_proven': False", "'custody_proven': True"),
        replace_python_syntax(raw, "'gate_a': 'BLOCKED'", "'gate_a': 'PASS'"),
        replace_python_syntax(raw, "'effect_permission': 'NONE'", "'effect_permission': 'GRANTED'"),
        replace_python_syntax(raw, "_public_key_fingerprint(public_key)", "None"),
        replace_python_syntax(raw, "_proc_start_time(_fd_read(fd, 'stat'))", "123"),
    ):
        target.write_text(mutation)
        assert _check_native_custody_observer_non_authority(tmp_path)
    target.write_text(raw)
    (tmp_path / "backend/src/oh_goon/runtime/alternate.py").write_text(
        "from oh_goon.repository_tooling import custody_host_observation\n"
    )
    assert _check_native_custody_observer_non_authority(tmp_path)
