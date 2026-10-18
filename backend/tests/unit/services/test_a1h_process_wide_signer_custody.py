"""A1h: prove any privileged Linux thread blocks original Fleet prework.

Synthetic kernel status responses here are negative-test stimuli, never
external custody attestation or production trust provenance.
"""
from __future__ import annotations

import os
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.services import fleet_prework_provisioner as owner
from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import facts
from tests.unit.services.test_group_a_fleet_provisioning_authority import provisioned


def _status(tid: int, *, pid: int = 77, uids: str = "1001\t1001\t1001\t1001",
            gids: str = "1001\t1001\t1001\t1001", groups: str = "1001 2002",
            nnp: str = "1", inh: str = "0", prm: str = "0", eff: str = "0",
            amb: str = "0") -> str:
    return (f"Name:\toh-goon\nPid:\t{tid}\nTgid:\t{pid}\nUid:\t{uids}\n"
            f"Gid:\t{gids}\nGroups:\t{groups}\nNoNewPrivs:\t{nnp}\n"
            f"CapInh:\t{inh}\nCapPrm:\t{prm}\nCapEff:\t{eff}\nCapAmb:\t{amb}\n")


def _stat(tid: int, *, started: int = 10999) -> str:
    return f"{tid} (oh-goon ) thread) S " + " ".join(["0"] * 18 + [str(started)]) + "\n"


def _kernel(monkeypatch, *, status: dict[int, str] | None = None,
            ids: list[list[str]] | None = None):
    entries = ids or [["111", "222"]]
    calls = 0
    monkeypatch.setattr(os, "getpid", lambda: 77)
    monkeypatch.setattr(threading, "get_native_id", lambda: 111)
    original_listdir = os.listdir
    original_read = Path.read_text

    def listdir(path):
        nonlocal calls
        if path == "/proc/self/task":
            selected = entries[min(calls, len(entries) - 1)]
            calls += 1
            return selected
        return original_listdir(path)

    def read_text(path, *args, **kwargs):
        if str(path).startswith("/proc/self/task/"):
            tid = int(path.parent.name)
            if path.name == "stat":
                return _stat(tid)
            return (status or {}).get(tid, _status(tid))
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(os, "listdir", listdir)
    monkeypatch.setattr(Path, "read_text", read_text)


def _census():
    return owner._native_signer_thread_census((1001,) * 4, (1001,) * 4, (1001, 2002))


def test_process_wide_credentials_include_every_linux_thread(monkeypatch):
    _kernel(monkeypatch)
    assert _census() == (77, 2, 111, 222, 10999, 10999)


@pytest.mark.parametrize("attack", [
    "saved_root", "effective_root", "filesystem_root", "foreign_gid",
    "wrong_groups", "nnp_disabled", "cap_inheritable", "cap_permitted",
    "cap_effective", "cap_ambient", "wrong_tgid", "wrong_pid",
    "duplicate_cap", "no_uid", "missing_task", "new_task", "bad_task_name",
    "caller_absent", "invalid_groups", "ambiguous_task",
])
def test_other_thread_privilege_or_inventory_race_fails_closed(monkeypatch, attack):
    statuses = {222: _status(222)}
    ids = None
    if attack == "saved_root":
        statuses[222] = _status(222, uids="1001 1001 0 1001")
    elif attack == "effective_root":
        statuses[222] = _status(222, uids="1001 0 0 0")
    elif attack == "filesystem_root":
        statuses[222] = _status(222, uids="1001 1001 1001 0")
    elif attack == "foreign_gid":
        statuses[222] = _status(222, gids="1001 1002 1001 1001")
    elif attack == "wrong_groups":
        statuses[222] = _status(222, groups="0 1001 2002")
    elif attack == "nnp_disabled":
        statuses[222] = _status(222, nnp="0")
    elif attack == "cap_inheritable":
        statuses[222] = _status(222, inh="1")
    elif attack == "cap_permitted":
        statuses[222] = _status(222, prm="1")
    elif attack == "cap_effective":
        statuses[222] = _status(222, eff="1")
    elif attack == "cap_ambient":
        statuses[222] = _status(222, amb="1")
    elif attack == "wrong_tgid":
        statuses[222] = _status(222, pid=999)
    elif attack == "wrong_pid":
        statuses[222] = _status(333)
    elif attack == "duplicate_cap":
        statuses[222] = _status(222) + "CapEff:\t0\n"
    elif attack == "no_uid":
        statuses[222] = _status(222).replace("Uid:", "Unknown:")
    elif attack == "missing_task":
        ids = [["111", "222"], ["111"]]
    elif attack == "new_task":
        ids = [["111", "222"], ["111", "222", "333"]]
    elif attack == "bad_task_name":
        ids = [["111", "not-a-tid"]]
    elif attack == "caller_absent":
        ids = [["222"]]
    elif attack == "invalid_groups":
        statuses[222] = _status(222, groups="1001 x")
    elif attack == "ambiguous_task":
        ids = [["111", "222", "222"]]
    _kernel(monkeypatch, status=statuses, ids=ids)
    with pytest.raises(TrustProtocolError):
        _census()


def test_original_fleet_admission_refuses_other_thread_root_before_work(tmp_path, monkeypatch):
    args, _params, conn, calls, lifecycle, binding, provisioner, _inventory, _roots, admit = provisioned(
        tmp_path, monkeypatch)
    try:
        with monkeypatch.context() as patches:
            # This fixture reaches the original provisioner but MUST NOT bypass
            # the thread census even if the current thread has valid credentials.
            patches.setattr(owner, "_native_unprivileged_signer_identity", lambda: (
                owner._native_signer_thread_census((1001,) * 4, (1001,) * 4, (1001, 2002))))
            _kernel(patches, status={222: _status(222, eff="1")})
            patches.setattr(os, "geteuid", lambda: 1001)
            lifecycle._fleet_provisioner = replace(provisioner, public_root_owner_uid=0)
            with pytest.raises(TrustProtocolError) as failure:
                admit()
            assert "signer thread retains" in str(failure.value), repr(failure.value.__cause__)
        assert facts(args, conn) == (None, 0, 0)
        assert conn.execute("SELECT COUNT(*) FROM execution_lease").fetchone()[0] == 0
        assert calls == []
    finally:
        conn.close()


def test_original_readonly_custody_cut_rejects_process_change_between_resolves(
    tmp_path, monkeypatch,
):
    _args, _params, conn, calls, _lifecycle, binding, provisioner, *_ = provisioned(
        tmp_path, monkeypatch)
    try:
        strict = replace(provisioner, public_root_owner_uid=65534)
        identities = iter(((1001,), (1002,)))
        with monkeypatch.context() as patches:
            patches.setattr(owner, "_native_unprivileged_signer_identity", lambda: next(identities))
            patches.setattr(owner.FleetPreworkProvisioner, "inspect_deployment_readiness",
                            lambda self, run: {"run_id": run, "executor_id": "e",
                                               "executor_generation": "g",
                                               "independently_verified_issuer_roots": 6})
            patches.setattr(owner.FleetPreworkProvisioner, "resolve", lambda self, run: binding)
            with pytest.raises(TrustProtocolError, match="principal changed"):
                strict.inspect_independent_root_custody("durable-run")
        assert calls == []
    finally:
        conn.close()
