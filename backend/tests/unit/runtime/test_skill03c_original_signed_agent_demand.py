"""SKILL-3C: exact Agent Step demand correlation at original signed Fleet cut.

These are DEVELOPMENT tests using fixture keys. No Agent Step is executable.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from oh_goon.core.enforcer_lace_trust import canonical_bytes, digest
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.runtime.enforcer_lace_journal import (
    CausalJournalError,
    CausalRunJournal,
    _event_identity,
)
from tests.unit.runtime.test_ag02b2b1_original_agent_source_reservation import fixture, reserve

EFFECT_STEP = """- uses: agent
  harness: opencode
  task: review-only
  requested_effects:
    repository_access: write
    recipients: [internal_model]
    capabilities:
      tools: [pytest, ruff]
      egress:
        mode: cidr_allowlist
        rules:
          - cidr: 10.0.0.0/8
            protocol: tcp
            ports: [443]
"""


def _no_journal_mutation_on_refusal(conn, journal, args):
    before = tuple(
        conn.execute("SELECT event_kind,event_payload FROM lace_run_events ORDER BY run_seq")
    )
    with pytest.raises((CausalJournalError, FleetPlanInvalid)):
        reserve(conn, journal, args)
    assert (
        tuple(conn.execute("SELECT event_kind,event_payload FROM lace_run_events ORDER BY run_seq"))
        == before
    )


def test_original_signed_run_correlates_exact_demand_and_keeps_zero_effect_authority(tmp_path):
    conn, journal, args, packet = fixture(tmp_path, effect_step=EFFECT_STEP)
    assert reserve(conn, journal, args) == 3
    verified = journal.verify("run-one", auth=args["auth"])
    binding = verified[-1]["payload"]["binding"]
    source = json.loads(packet)["step"]["requested_effects"]
    assert binding["requested_effects_identity"] == digest(
        "oh-goon/agent-requested-effects/v1",
        canonical_bytes(source),
    )
    assert binding["authority"] == "PREWORK_NOT_ADMITTED"
    assert binding["effect_permission"] == "NONE"
    assert "requested_effects" not in binding  # do not journal plaintext demands
    assert b"internal_model" not in verified[-1]["payload"].__str__().encode()
    conn.close()
    reopened = sqlite3.connect(tmp_path / "authority.sqlite", isolation_level=None)
    assert (
        CausalRunJournal(reopened).verify("run-one", auth=args["auth"])[-1]["payload"]["binding"]
        == binding
    )
    reopened.close()


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        {
            "repository_access": "read_only",
            "recipients": ["internal_model"],
            "capabilities": {"tools": ["pytest", "ruff"], "egress": {"mode": "none"}},
        },
        {
            "repository_access": "write",
            "recipients": ["external_model"],
            "capabilities": {"tools": ["pytest", "ruff"], "egress": {"mode": "none"}},
        },
        {
            "repository_access": "write",
            "recipients": ["internal_model"],
            "capabilities": {"tools": [], "egress": {"mode": "host_unrestricted"}},
        },
    ],
)
def test_missing_or_different_original_enforcer_demand_refuses_before_reservation(tmp_path, change):
    conn, journal, args, _ = fixture(tmp_path, effect_step=EFFECT_STEP, envelope_effects=change)
    _no_journal_mutation_on_refusal(conn, journal, args)
    conn.close()


def test_legacy_default_run_cannot_smuggle_a_demand(tmp_path):
    conn, journal, args, _ = fixture(
        tmp_path,
        envelope_effects={
            "repository_access": "write",
            "recipients": ["internal_model"],
            "capabilities": {"tools": ["ruff"], "egress": {"mode": "none"}},
        },
    )
    _no_journal_mutation_on_refusal(conn, journal, args)
    conn.close()


def test_original_signed_prework_demand_refuses_forged_journal_hash_even_with_rehashed_chain(
    tmp_path,
):
    conn, journal, args, _ = fixture(tmp_path, effect_step=EFFECT_STEP)
    assert reserve(conn, journal, args) == 3
    raw = conn.execute(
        "SELECT event_payload,previous_identity FROM lace_run_events WHERE run_seq=3"
    ).fetchone()
    forged = json.loads(raw[0])
    forged["binding"]["requested_effects_identity"] = "sha256:" + "0" * 64
    encoded = canonical_bytes(forged)
    event_identity = _event_identity("run-one", 3, "AGENT_SOURCE_PREWORK_RESERVED", encoded, raw[1])
    conn.execute(
        "UPDATE lace_run_events SET event_payload=?,event_identity=? WHERE run_seq=3",
        (encoded, event_identity),
    )
    conn.execute(
        "UPDATE lace_run_authority SET head_identity=? WHERE run_id=?", (event_identity, "run-one")
    )
    with pytest.raises(CausalJournalError, match="demand identity"):
        journal.verify("run-one", auth=args["auth"])
    conn.close()


def test_even_a_resigned_prework_event_cannot_override_original_run_scope(tmp_path):
    """Authentication alone cannot supersede the original signed Run/Step contract."""
    conn, journal, args, _ = fixture(tmp_path, effect_step=EFFECT_STEP)
    assert reserve(conn, journal, args) == 3
    raw = conn.execute(
        "SELECT event_payload,previous_identity FROM lace_run_events WHERE run_seq=3"
    ).fetchone()
    altered = json.loads(raw[0])
    binding = altered["binding"]
    binding["requested_effects_identity"] = "sha256:" + "b" * 64
    # TEST issuer key is deliberately available to prove *semantic* validation,
    # independent of cryptographic signature validity.
    signed = args["auth"].issue("agent-source-prework", binding)
    altered["signature"] = signed.mac_hex
    encoded = canonical_bytes(altered)
    identity = _event_identity("run-one", 3, "AGENT_SOURCE_PREWORK_RESERVED", encoded, raw[1])
    conn.execute(
        "UPDATE lace_run_events SET event_payload=?,event_identity=? WHERE run_seq=3",
        (encoded, identity),
    )
    conn.execute(
        "UPDATE lace_run_authority SET head_identity=? WHERE run_id=?", (identity, "run-one")
    )
    with pytest.raises(CausalJournalError, match="demand identity"):
        journal.verify("run-one", auth=args["auth"])
    conn.close()


def test_old_non_effect_packet_and_signed_event_shape_remain_stable(tmp_path):
    conn, journal, args, _ = fixture(tmp_path)
    assert reserve(conn, journal, args) == 3
    binding = journal.verify("run-one", auth=args["auth"])[-1]["payload"]["binding"]
    assert "requested_effects_identity" not in binding
    assert binding["effect_permission"] == "NONE"
    conn.close()
