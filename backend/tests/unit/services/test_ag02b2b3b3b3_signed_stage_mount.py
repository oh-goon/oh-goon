"""Original signed staged mount identity: no alternate owner or effect permission."""

from __future__ import annotations

from dataclasses import replace

import pytest

from oh_goon.core.ports.original_mount_intent import OriginalSandboxMount
from oh_goon.integrations.fleet_plan_run import FleetPlanStepAttempt
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_original_signed_stage_mount,
)
from tests.factories.x0_architecture import replace_python_syntax
from tests.fixtures.db1b2_legacy_sqlite import CausalJournalError
from tests.unit.services.test_ag02b2b3b2_original_mount_intent_preflight import live_case


def test_original_signed_stage_contains_exact_prework_mount_without_effect_grant(
    tmp_path, monkeypatch
):
    conn, journal, lifecycle, session, expected, inspect, events, args = live_case(
        tmp_path, monkeypatch
    )
    signed = journal.verify("run-one", auth=args["auth"])[-1]["payload"]["binding"]
    proposal = lifecycle.inspect_original_agent_source_mount_for_attempt(
        session, expected, **inspect
    )
    mount = proposal.sandbox_bindings[0]
    assert signed["source_mount"] == {
        "source": mount.source,
        "target": mount.target,
        "sha256": mount.sha256,
    }
    assert signed["stage"]["effect_permission"] == "NONE"
    assert signed["effect_permission"] == "NONE"
    assert signed["physical_sandbox_proven"] is False
    assert not any(
        e["kind"] == "RESOURCE_INTENT" for e in journal.verify("run-one", auth=args["auth"])
    )
    conn.close()


def test_original_stage_source_substitution_with_valid_signature_is_rejected(tmp_path, monkeypatch):
    normal = FleetPlanStepAttempt._inspect_agent_prework_readonly_mount

    def forged(self, handoff, stage):
        original = normal(self, handoff, stage)
        return replace(original, sha256="f" * 64)

    with monkeypatch.context() as patch:
        patch.setattr(FleetPlanStepAttempt, "_inspect_agent_prework_readonly_mount", forged)
        conn, journal, lifecycle, session, expected, inspect, events, args = live_case(
            tmp_path, monkeypatch
        )
    assert (
        journal.verify("run-one", auth=args["auth"])[-1]["payload"]["binding"]["source_mount"][
            "sha256"
        ]
        == "f" * 64
    )
    before = list(conn.execute("SELECT event_payload FROM lace_run_events"))
    with pytest.raises(CausalJournalError, match="does not match original signed stage"):
        lifecycle.inspect_original_agent_source_mount_for_attempt(session, expected, **inspect)
    assert before == list(conn.execute("SELECT event_payload FROM lace_run_events"))
    conn.close()


def test_legacy_signed_stage_is_historical_only_not_mount_authority(tmp_path, monkeypatch):
    from oh_goon.runtime.enforcer_lace_journal import CausalRunJournal as RuntimeCausalRunJournal

    real = RuntimeCausalRunJournal.record_agent_prework_stage_in

    def legacy(self, *a, **kw):
        kw.pop("source_mount", None)
        return real(self, *a, **kw)

    with monkeypatch.context() as patch:
        patch.setattr(RuntimeCausalRunJournal, "record_agent_prework_stage_in", legacy)
        conn, journal, lifecycle, session, expected, inspect, events, args = live_case(
            tmp_path, monkeypatch
        )
    signed = journal.verify("run-one", auth=args["auth"])[-1]["payload"]["binding"]
    assert "source_mount" not in signed
    with pytest.raises(CausalJournalError, match="does not match original signed stage"):
        lifecycle.inspect_original_agent_source_mount_for_attempt(session, expected, **inspect)
    conn.close()


def test_source_mount_rejects_foreign_target_before_signature(tmp_path, monkeypatch):
    from tests.unit.runtime.test_ag02b2b1_original_agent_source_reservation import fixture, reserve

    conn, journal, args, _ = fixture(tmp_path)
    reserve(conn, journal, args)
    stage = {
        "schema": "oh-goon.agent-prework-attempt-stage/v1",
        "handoff_identity": args["expected_handoff_identity"],
        "stage_identity": "sha256:" + "c" * 64,
        "file_count": 2,
        "readback_verified": True,
        "effect_permission": "NONE",
        "physical_sandbox_proven": False,
    }
    conn.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(CausalJournalError, match="fixed sandbox target"):
            journal.record_agent_prework_stage_in(
                "run-one",
                epoch=1,
                lease_identity="lease-one",
                auth=args["auth"],
                reservation_seq=3,
                step_id=args["step_id"],
                attempt_identity=args["attempt_identity"],
                stage=stage,
                source_mount=OriginalSandboxMount("/tmp/source", "/tmp/escape", "d" * 64),
            )
    finally:
        conn.execute("ROLLBACK")
    conn.close()


def test_x0_stage_mount_owner_and_mutations(tmp_path):
    source = ROOT / "backend/src/oh_goon"
    assert _check_original_signed_stage_mount(source) == []
    journal = (source / "runtime/enforcer_lace_journal.py").read_text()
    fleet = (source / "services/fleet_run_lifecycle.py").read_text()
    mutations = [
        (
            "journal",
            replace_python_syntax(
                journal, 'binding["source_mount"] = {', 'binding["unused_mount"] = {'
            ),
        ),
        (
            "journal",
            replace_python_syntax(
                journal, 'checked_mount.target != "/opt/oh-goon/agent-source"', "False"
            ),
        ),
        ("fleet", replace_python_syntax(fleet, "source_mount=source_mount,", "source_mount=None,")),
        (
            "fleet",
            replace_python_syntax(fleet, "signed_stage.get('source_mount') != {", "None != {"),
        ),
    ]
    for i, (which, content) in enumerate(mutations):
        root = tmp_path / str(i)
        (root / "runtime").mkdir(parents=True)
        (root / "services").mkdir()
        (root / "runtime/enforcer_lace_journal.py").write_text(
            content if which == "journal" else journal
        )
        (root / "services/fleet_run_lifecycle.py").write_text(
            content if which == "fleet" else fleet
        )
        assert _check_original_signed_stage_mount(root), (which, i)
