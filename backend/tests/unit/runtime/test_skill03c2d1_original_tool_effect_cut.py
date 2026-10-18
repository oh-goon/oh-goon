"""First original Fleet/LACE tool-effect cut; this is NOT an execution grant.

The original AG-02 source must still be staged and the mandatory original
RESOURCE_INTENT refusal must remain. Hosted fixtures are not kernel evidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from oh_goon.services.agent_authoring_sources import reserve_verified_agent_source_in
from tests.unit.runtime.test_ag02b2b1_original_agent_source_reservation import fixture
from tests.unit.runtime.test_ag02b2b3_original_agent_attempt_staging import attempt
from tests.unit.runtime.test_b12_local_resource_intent import _resources

TOOL_STEP = """- uses: agent
  harness: opencode
  task: inspect-repository
  requested_effects:
    repository_access: read_only
    capabilities:
      tools: [pytest]
"""


def _staged(tmp_path, step=TOOL_STEP, *, sign_mount=True):
    conn, journal, args, packet = fixture(tmp_path, effect_step=step)
    handoff = FrozenAgentStepHandoff.decode_prework_delivery(
        packet,
        expected_handoff_identity=args["expected_handoff_identity"],
    )
    owner = attempt(tmp_path, handoff)
    conn.execute("BEGIN IMMEDIATE")
    try:

        def stage(decoded, seq):
            staged = owner._stage_agent_prework_bytes(decoded)
            source_mount = (
                owner._inspect_agent_prework_readonly_mount(decoded, staged) if sign_mount else None
            )
            journal.record_agent_prework_stage_in(
                "run-one",
                epoch=1,
                lease_identity="lease-one",
                auth=args["auth"],
                reservation_seq=seq,
                step_id=args["step_id"],
                attempt_identity=args["attempt_identity"],
                stage=staged,
                source_mount=source_mount,
            )

        reserve_verified_agent_source_in(
            journal,
            "run-one",
            **args,
            after_reservation=stage,
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    args["operational_attempt_root"] = owner.paths.root
    return conn, journal, args


def _inspect(conn, journal, args, **changes):
    original = dict(
        epoch=1,
        lease_identity="lease-one",
        auth=args["auth"],
        step_id=args["step_id"],
        operational_attempt_identity=args["attempt_identity"],
        operational_attempt_root=args.get(
            "operational_attempt_root", Path("/missing-agent-attempt")
        ),
        argv=("/toolchain/bin/pytest", "--version"),
        mounted_tool_names=("pytest",),
        actual_egress={"mode": "none"},
        read_only_workspace=True,
    )
    original.update(changes)
    conn.execute("BEGIN IMMEDIATE")
    try:
        return journal.inspect_original_agent_tool_launch_scope_in("run-one", **original)
    finally:
        conn.execute("ROLLBACK")


def test_exact_read_only_offline_tool_scopes_are_checked_but_never_granted(tmp_path):
    conn, journal, args = _staged(tmp_path)
    before = journal.verify("run-one", auth=args["auth"])
    assert _inspect(conn, journal, args) is None  # not a capability or grant
    assert journal.verify("run-one", auth=args["auth"]) == before
    conn.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(CausalJournalError, match="no qualified signed RESOURCE_INTENT"):
            journal.publish_local_resource_intent_in(
                "run-one",
                epoch=1,
                lease_identity="lease-one",
                auth=args["auth"],
                step_id=args["step_id"],
                attempt_identity=args["attempt_identity"],
                execution_environment_identity="sha256:" + "a" * 64,
                backend_capability_manifest_identity="sha256:" + "b" * 64,
                resources=_resources(),
            )
    finally:
        conn.execute("ROLLBACK")
    assert journal.verify("run-one", auth=args["auth"]) == before
    conn.close()


@pytest.mark.parametrize(
    "attack,changes",
    [
        ("unmounted", {"mounted_tool_names": ("ruff",)}),
        ("extra-tool", {"mounted_tool_names": ("pytest", "ruff")}),
        ("duplicate-tool", {"mounted_tool_names": ("pytest", "pytest")}),
        ("path-bypass", {"argv": ("/tmp/pytest",)}),
        ("path-search", {"argv": ("pytest",)}),
        ("shell-bypass", {"argv": ("/bin/sh", "-c", "pytest")}),
        ("alternate-tool", {"argv": ("/toolchain/bin/ruff",)}),
        ("writable-workspace", {"read_only_workspace": False}),
        ("host-egress", {"actual_egress": {"mode": "host_unrestricted", "rules": []}}),
        ("wrong-epoch", {"epoch": 2}),
        ("wrong-lease", {"lease_identity": "other"}),
    ],
)
def test_unscoped_agent_native_tool_effect_refused_without_journal_mutation(
    tmp_path, attack, changes
):
    conn, journal, args = _staged(tmp_path)
    before = journal.verify("run-one", auth=args["auth"])
    with pytest.raises(CausalJournalError):
        _inspect(conn, journal, args, **changes)
    assert journal.verify("run-one", auth=args["auth"]) == before
    conn.close()


@pytest.mark.parametrize(
    "step",
    [
        """- uses: agent
  harness: opencode
  task: review
  requested_effects:
    repository_access: write
    capabilities:
      tools: [pytest]
""",
        """- uses: agent
  harness: opencode
  task: review
  requested_effects:
    recipients: [internal_model]
    capabilities:
      tools: [pytest]
""",
        """- uses: agent
  harness: opencode
  task: review
  requested_effects:
    capabilities:
      tools: [pytest]
      egress:
        mode: cidr_allowlist
        rules:
          - cidr: 10.0.0.0/8
            protocol: tcp
            ports: [443]
""",
    ],
)
def test_unimplemented_write_disclosure_and_network_cuts_never_gain_authority(tmp_path, step):
    conn, journal, args = _staged(tmp_path, step=step)
    with pytest.raises(CausalJournalError, match="unenforced"):
        _inspect(conn, journal, args)
    conn.close()


def test_signer_revocation_fences_tool_cut(tmp_path):
    conn, journal, args = _staged(tmp_path)
    conn.execute("UPDATE lace_signer_trust_epoch SET state='REVOKED'")
    with pytest.raises((CausalJournalError, TrustProtocolError), match="signer|epoch|trust"):
        _inspect(conn, journal, args)
    conn.close()


def test_original_agent_tool_scope_refuses_missing_signed_stage(tmp_path):
    from tests.unit.runtime.test_ag02b2b1_original_agent_source_reservation import reserve

    conn, journal, args, _ = fixture(tmp_path, effect_step=TOOL_STEP)
    reserve(conn, journal, args)
    with pytest.raises(CausalJournalError, match="staged"):
        _inspect(conn, journal, args)
    conn.close()


def test_original_fleet_lace_effect_uses_one_existing_admission_owner():
    import ast

    from oh_goon.repository_tooling.architecture.repository_layout import ROOT

    p = ROOT / "backend/src/oh_goon/runtime/enforcer_lace_fleet_resource_owner.py"
    tree = ast.parse(p.read_text())
    owners = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "reserve"
    ]
    original = next(
        node
        for node in owners
        if any(
            isinstance(n, ast.Name) and n.id == "LocalLaceJournalReservation"
            for n in ast.walk(node)
        )
    )
    calls = [
        n.func.attr
        for n in ast.walk(original)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    ]
    assert calls.count("issue_original_agent_tool_launch_grant_in") == 1
    assert calls.count("mutate") == 1


def test_operational_attempt_must_equal_original_signed_source_attempt(tmp_path):
    conn, journal, args = _staged(tmp_path)
    before = journal.verify("run-one", auth=args["auth"])
    with pytest.raises(CausalJournalError, match="operational Step attempt"):
        _inspect(conn, journal, args, operational_attempt_identity="sha256:" + "f" * 64)
    assert journal.verify("run-one", auth=args["auth"]) == before
    conn.close()


@pytest.mark.parametrize(
    "attempt_root",
    [
        Path("/unrelated/attempt/01"),
        Path("/tmp/same-step/02"),
    ],
)
def test_agent_stage_cannot_be_laundered_into_another_attempt_root(tmp_path, attempt_root):
    conn, journal, args = _staged(tmp_path)
    before = journal.verify("run-one", auth=args["auth"])
    with pytest.raises(CausalJournalError, match="source mount differs"):
        _inspect(conn, journal, args, operational_attempt_root=attempt_root)
    assert journal.verify("run-one", auth=args["auth"]) == before
    conn.close()


def test_legacy_source_receipt_without_signed_mount_does_not_upgrade_to_launch(tmp_path):
    conn, journal, args = _staged(tmp_path, sign_mount=False)
    before = journal.verify("run-one", auth=args["auth"])
    with pytest.raises(CausalJournalError, match="source mount differs"):
        _inspect(conn, journal, args)
    assert journal.verify("run-one", auth=args["auth"]) == before
    conn.close()


def test_agent_original_fleet_reservation_refuses_without_attempt_fact_owner(tmp_path):
    from types import SimpleNamespace

    from oh_goon.runtime.enforcer_lace_fleet_resource_owner import OriginalFleetLocalLaceReservation

    conn, journal, args = _staged(tmp_path)
    session = SimpleNamespace(
        mutate=lambda action: _run_original_cut(conn, action),
        cancel_requested_in=lambda _: False,
        original_fleet_lease_token=lambda: "lease-one",
    )
    owner = OriginalFleetLocalLaceReservation(
        session=session,
        validate_authority_in=lambda _: None,
        authenticator=args["auth"],
        run_id="run-one",
        step_id=args["step_id"],
        attempt_number=1,
        command_ordinal=1,
        attempt_root=args["operational_attempt_root"],
        execution_environment_identity="sha256:" + "a" * 64,
        backend_capability_manifest_identity="sha256:" + "b" * 64,
        backend_configuration_identity="sha256:" + "c" * 64,
        agent_workload=True,
    )
    attempt = SimpleNamespace(
        host_tools=(SimpleNamespace(name="pytest"),),
        egress=SimpleNamespace(model_dump=lambda **kwargs: {"mode": "none"}),
    )
    request = SimpleNamespace(
        argv=("/toolchain/bin/pytest",),
        attempt=attempt,
        read_only_workspace=object(),
    )
    before = journal.verify("run-one", auth=args["auth"])
    with pytest.raises(CausalJournalError, match="operational attempt authority missing"):
        owner.reserve(request, object())
    assert journal.verify("run-one", auth=args["auth"]) == before
    conn.close()


def _run_original_cut(connection, action):
    connection.execute("BEGIN IMMEDIATE")
    try:
        return action(connection)
    finally:
        connection.execute("ROLLBACK")
