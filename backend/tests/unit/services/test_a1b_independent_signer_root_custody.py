"""A1b: signer and verifier roots cannot be physically the same authority tree."""

from __future__ import annotations

from dataclasses import replace

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.services import fleet_prework_provisioner as owner_module
from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import facts
from tests.unit.services.test_group_a_fleet_provisioning_authority import provisioned


@pytest.mark.parametrize("case", ["same", "root-inside-signer", "signer-inside-root"])
def test_aliased_public_and_signer_tree_rejects_before_native_observation(
    tmp_path, monkeypatch, case,
):
    args, _params, conn, calls, _lifecycle, binding, provisioner, _inventory, _roots, _admit = provisioned(tmp_path, monkeypatch)
    try:
        def must_not_observe():
            pytest.fail("inventory observation began with aliased custody")

        monkeypatch.setattr(owner_module, "_inventory_snapshot", must_not_observe)
        if case == "same":
            owner = replace(provisioner, trusted_key_directory=binding.key_directory)
        elif case == "root-inside-signer":
            public = binding.key_directory / "trusted"
            public.mkdir(mode=0o700)
            owner = replace(provisioner, trusted_key_directory=public)
        else:
            signer = provisioner.trusted_key_directory / "private"
            signer.mkdir(mode=0o700)
            owner = replace(provisioner, enrolled=(replace(binding, key_directory=signer),))
        with pytest.raises(TrustProtocolError, match="separate from signer custody"):
            owner.resolve("durable-run")
        assert facts(args, conn) == (None, 0, 0)
        assert calls == []
    finally:
        conn.close()