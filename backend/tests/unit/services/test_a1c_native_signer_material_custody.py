"""A1c: missing/replaced private material is rejected by the original native signer owner.

The prepared Fleet enrollment and file fixtures are test material, NOT real
production signer custody or native host qualification.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.native_issuer import enforcer_lace_signer_custody as custody
from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import facts
from tests.unit.services.test_group_a_fleet_provisioning_authority import provisioned


def _policy_and_leaf(binding):
    policy_path = binding.key_directory / "signers.json"
    policy = json.loads(policy_path.read_bytes())
    chosen = policy["issuers"]["source_identity"]["private_key_file"]
    return policy_path, policy, binding.key_directory / chosen


def _replace_identical(path: Path) -> None:
    successor = path.with_name(path.name + ".replacement")
    successor.write_bytes(path.read_bytes())
    successor.chmod(0o600)
    os.replace(successor, path)


@pytest.mark.parametrize(
    "attack",
    [
        "missing-seed",
        "invalid-seed",
        "wrong-seed",
        "weak-seed",
        "symlink-seed",
        "policy-swap-after-read",
        "seed-swap-after-read",
    ],
)
def test_prework_rejects_bad_six_domain_native_signer_custody_before_work(
    tmp_path,
    monkeypatch,
    attack,
):
    args, _params, conn, calls, _lifecycle, binding, owner, _inventory, _roots, _admit = (
        provisioned(tmp_path, monkeypatch)
    )
    policy_path, _policy, private = _policy_and_leaf(binding)
    try:
        if attack == "missing-seed":
            private.unlink()
        elif attack == "invalid-seed":
            private.write_bytes(b"short")
        elif attack == "wrong-seed":
            private.write_bytes(b"z" * 32)
        elif attack == "weak-seed":
            private.chmod(0o644)
        elif attack == "symlink-seed":
            original = private.read_bytes()
            private.unlink()
            another = binding.key_directory / "outside.seed"
            another.write_bytes(original)
            another.chmod(0o600)
            private.symlink_to(another)
        else:
            original_read = custody._read_leaf
            replaced = False

            def raced_read(root_fd, filename, *, limit):
                nonlocal replaced
                result = original_read(root_fd, filename, limit=limit)
                if not replaced and filename == (
                    "signers.json" if attack == "policy-swap-after-read" else private.name
                ):
                    replaced = True
                    _replace_identical(
                        policy_path if attack == "policy-swap-after-read" else private
                    )
                return result

            monkeypatch.setattr(custody, "_read_leaf", raced_read)
        with pytest.raises(TrustProtocolError):
            owner.inspect_deployment_readiness("durable-run")
        assert facts(args, conn) == (None, 0, 0)
        assert conn.execute("SELECT COUNT(*) FROM execution_lease").fetchone()[0] == 0
        assert calls == []
    finally:
        conn.close()


def test_custody_probe_checks_all_six_seeds_without_signing_or_work(tmp_path, monkeypatch):
    args, _params, conn, calls, _lifecycle, binding, owner, _inventory, _roots, _admit = (
        provisioned(tmp_path, monkeypatch)
    )
    try:
        result = owner.inspect_deployment_readiness("durable-run")
        assert result["status"] == "OBSERVED_NOT_ADMITTED"
        assert result["independently_verified_issuer_roots"] == 6
        assert result["run_admission"] == "NOT_PERFORMED"
        assert "signers.json" not in str(dict(result))
        assert facts(args, conn) == (None, 0, 0)
        assert calls == []
    finally:
        conn.close()


def test_native_leaf_name_swap_during_descriptor_read_fails_closed(tmp_path, monkeypatch):
    args, _params, conn, calls, _lifecycle, binding, owner, _inventory, _roots, _admit = (
        provisioned(tmp_path, monkeypatch)
    )
    _policy_path, _policy, private = _policy_and_leaf(binding)
    real_read = os.read
    swapped = False

    def swap_after_fd_read(fd, size):
        nonlocal swapped
        data = real_read(fd, size)
        if not swapped and os.path.realpath(f"/proc/self/fd/{fd}") == str(private):
            swapped = True
            _replace_identical(private)
        return data

    try:
        monkeypatch.setattr(custody.os, "read", swap_after_fd_read)
        with pytest.raises(TrustProtocolError, match="name changed"):
            owner.inspect_deployment_readiness("durable-run")
        assert swapped
        assert facts(args, conn) == (None, 0, 0)
        assert calls == []
    finally:
        conn.close()


@pytest.mark.parametrize(
    "field",
    [
        "source_identity",
        "lock_identity",
        "native_tool_identity",
        "backend_capability_manifest_identity",
        "execution_environment_identity",
        "repository_identity",
    ],
)
def test_each_native_signer_domain_must_match_independent_root(tmp_path, monkeypatch, field):
    args, _params, conn, calls, _lifecycle, binding, owner, _inventory, _roots, _admit = (
        provisioned(tmp_path, monkeypatch)
    )
    try:
        policy = json.loads((binding.key_directory / "signers.json").read_bytes())
        key = binding.key_directory / policy["issuers"][field]["private_key_file"]
        key.write_bytes(b"\x03" * 32)
        with pytest.raises(TrustProtocolError, match="differs from independent public root"):
            owner.inspect_deployment_readiness("durable-run")
        assert facts(args, conn) == (None, 0, 0)
        assert calls == []
    finally:
        conn.close()
