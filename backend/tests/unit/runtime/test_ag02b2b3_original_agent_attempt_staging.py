"""AG-02B2B3 hosted prework staging; these are not physical LACE Gate B tests."""

from __future__ import annotations

import stat

import pytest

from oh_goon.core.enforcer_lace_trust import canonical_bytes
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
from oh_goon.core.path_layouts import AttemptPathLayout
from oh_goon.integrations.fleet_plan_run import FleetPlanStepAttempt
from oh_goon.services.agent_authoring_sources import reserve_verified_agent_source_in
from tests.fixtures.db1b2_legacy_sqlite import CausalJournalError, _event_identity
from tests.unit.runtime.test_ag02b2b1_original_agent_source_reservation import fixture
from tests.unit.services.test_ag02b_typed_agent_prework_handoff import _case


def original_handoff(tmp_path):
    resolver, plan, kw, _, _ = _case(tmp_path)
    return resolver.prepare_agent_step_handoff(plan, **kw)


def attempt(tmp_path, handoff):
    root = tmp_path / "attempts" / handoff.step_id / "01"
    root.mkdir(parents=True, mode=0o700)
    return FleetPlanStepAttempt(
        paths=AttemptPathLayout.from_root(root),
        cache_identity=None,
        egress=None,
        host_tools=(),
        sandbox_bindings=(),
        managed_runtime=None,
        host_environment={},
        runtime_root=None,
        isolation_backend=None,
        resources=None,
        nested_strict_execution=False,
        executable_materialization=None,
        attempt_number=1,
    )


def test_original_attempt_private_stage_has_complete_byte_exact_readback(tmp_path):
    handoff = original_handoff(tmp_path)
    owner = attempt(tmp_path, handoff)
    packet = handoff.encode_prework_delivery()
    # Packet-only recipient: source files may disappear without affecting output.
    stage = owner._stage_agent_prework_bytes(
        type(handoff).decode_prework_delivery(
            packet, expected_handoff_identity=handoff.handoff_identity
        )
    )
    assert stage["readback_verified"] is True
    assert stage["effect_permission"] == "NONE"
    assert stage["physical_sandbox_proven"] is False
    assert stage["file_count"] == len(handoff.source.repository.members) + len(
        handoff.source.skill.members
    )
    root = owner.paths.root / "agent-source-prework"
    assert stat.S_IMODE(root.stat().st_mode) == 0o500
    for tree in (handoff.source.repository, handoff.source.skill):
        for member in tree.members:
            staged = root / tree.kind / member.path
            assert staged.read_bytes() == member.data
            assert stat.S_IMODE(staged.stat().st_mode) == 0o400
            assert staged.stat().st_nlink == 1
    with pytest.raises(FleetPlanInvalid, match="failed closed"):
        owner._stage_agent_prework_bytes(handoff)


def test_hostile_stage_inputs_fail_closed_before_bytes(tmp_path):
    handoff = original_handoff(tmp_path)
    owner = attempt(tmp_path, handoff)
    # Never follow an attacker-supplied attempt-root symlink.
    root = owner.paths.root
    real = root.with_name("real")
    root.rename(real)
    root.symlink_to(real, target_is_directory=True)
    with pytest.raises(FleetPlanInvalid, match="root unavailable"):
        owner._stage_agent_prework_bytes(handoff)
    assert not (real / "agent-source-prework").exists()


def test_staging_disallows_public_attempt_root(tmp_path):
    handoff = original_handoff(tmp_path)
    owner = attempt(tmp_path, handoff)
    owner.paths.root.chmod(0o755)
    with pytest.raises(FleetPlanInvalid, match="not private"):
        owner._stage_agent_prework_bytes(handoff)
    assert not (owner.paths.root / "agent-source-prework").exists()


def test_stage_is_signed_into_one_original_journal_and_contains_no_source(tmp_path):
    conn, journal, args, packet = fixture(tmp_path)
    handoff = FrozenAgentStepHandoff.decode_prework_delivery(
        packet, expected_handoff_identity=args["expected_handoff_identity"]
    )
    owner = attempt(tmp_path, handoff)
    conn.execute("BEGIN IMMEDIATE")
    try:

        def after_reservation(decoded, seq):
            stage = owner._stage_agent_prework_bytes(decoded)
            journal.record_agent_prework_stage_in(
                "run-one",
                epoch=1,
                lease_identity="lease-one",
                auth=args["auth"],
                reservation_seq=seq,
                step_id=args["step_id"],
                attempt_identity=args["attempt_identity"],
                stage=stage,
            )

        assert (
            reserve_verified_agent_source_in(
                journal,
                "run-one",
                **args,
                after_reservation=after_reservation,
            )
            == 3
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    events = journal.verify("run-one", auth=args["auth"])
    assert [e["kind"] for e in events][-2:] == [
        "AGENT_SOURCE_PREWORK_RESERVED",
        "AGENT_SOURCE_PREWORK_STAGED",
    ]
    assert events[-1]["payload"]["binding"]["stage"]["physical_sandbox_proven"] is False
    journal_bytes = b"".join(
        r[0] for r in conn.execute("SELECT event_payload FROM lace_run_events")
    )
    assert b"private source" not in journal_bytes
    assert packet not in journal_bytes
    before = tuple(conn.execute("SELECT event_payload FROM lace_run_events"))
    with pytest.raises(CausalJournalError, match="already recorded"):
        conn.execute("BEGIN IMMEDIATE")
        try:
            journal.record_agent_prework_stage_in(
                "run-one",
                epoch=1,
                lease_identity="lease-one",
                auth=args["auth"],
                reservation_seq=3,
                step_id=args["step_id"],
                attempt_identity=args["attempt_identity"],
                stage=events[-1]["payload"]["binding"]["stage"],
            )
        finally:
            conn.execute("ROLLBACK")
    assert tuple(conn.execute("SELECT event_payload FROM lace_run_events")) == before
    # A chain-hash rewrite cannot forge the original issuer's signature.
    altered = dict(events[-1]["payload"])
    altered["binding"] = dict(altered["binding"])
    altered["binding"]["stage"] = dict(altered["binding"]["stage"])
    altered["binding"]["stage"]["file_count"] += 1
    raw = canonical_bytes(altered)
    prev = conn.execute("SELECT previous_identity FROM lace_run_events WHERE run_seq=4").fetchone()[
        0
    ]
    ident = _event_identity("run-one", 4, "AGENT_SOURCE_PREWORK_STAGED", raw, prev)
    conn.execute(
        "UPDATE lace_run_events SET event_payload=?,event_identity=? WHERE run_seq=4", (raw, ident)
    )
    conn.execute("UPDATE lace_run_authority SET head_identity=? WHERE run_id='run-one'", (ident,))
    with pytest.raises(Exception, match="authentication|signature"):
        journal.verify("run-one", auth=args["auth"])
    conn.close()


def test_staging_crash_before_commit_fails_closed(tmp_path):
    conn, journal, args, packet = fixture(tmp_path)
    handoff = FrozenAgentStepHandoff.decode_prework_delivery(
        packet, expected_handoff_identity=args["expected_handoff_identity"]
    )
    owner = attempt(tmp_path, handoff)
    conn.execute("BEGIN IMMEDIATE")
    try:
        reserve_verified_agent_source_in(
            journal,
            "run-one",
            **args,
            after_reservation=lambda decoded, _: owner._stage_agent_prework_bytes(decoded),
        )
        raise RuntimeError("simulated crash")
    except RuntimeError:
        conn.execute("ROLLBACK")
    assert len(journal.verify("run-one", auth=args["auth"])) == 2
    # Native filesystem staging was NOT transactional, cannot be reused or
    # interpreted as durable custody: requires original cleanup/reconciliation.
    with pytest.raises(FleetPlanInvalid, match="failed closed"):
        owner._stage_agent_prework_bytes(handoff)
    conn.close()


def test_x0_one_canonical_source_decoder_and_original_attempt_stager():
    """A duplicate agent-specific runner or staging service must not emerge."""
    import ast

    from oh_goon.repository_tooling.architecture.repository_layout import ROOT

    base = ROOT / "backend/src/oh_goon"
    service = ast.parse((base / "services/agent_authoring_sources.py").read_text())
    lifecycle = ast.parse((base / "services/fleet_run_lifecycle.py").read_text())
    integration = ast.parse((base / "integrations/fleet_plan_run.py").read_text())
    receivers = [
        n
        for n in ast.walk(service)
        if isinstance(n, ast.FunctionDef) and n.name == "reserve_verified_agent_source_in"
    ]
    assert len(receivers) == 1
    assert (
        sum(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "decode_prework_delivery"
            for n in ast.walk(receivers[0])
        )
        == 1
    )
    stages = [
        n
        for n in ast.walk(integration)
        if isinstance(n, ast.FunctionDef) and n.name == "_stage_agent_prework_bytes"
    ]
    assert len(stages) == 1
    assert (
        sum(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "_stage_agent_prework_bytes"
            for n in ast.walk(lifecycle)
        )
        == 1
    )
    assert not any(
        isinstance(n, (ast.Import, ast.ImportFrom)) and "subprocess" in ast.unparse(n)
        for n in ast.walk(receivers[0])
    )
