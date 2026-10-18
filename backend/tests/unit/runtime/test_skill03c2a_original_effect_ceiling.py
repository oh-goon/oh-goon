"""SKILL-3C2A: independently signed limits narrow Agent requests; no execution.

No separate scheduler, policy authority, signer, journal or effect grant exists.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from oh_goon.core.enforcer_lace_trust import canonical_bytes, digest
from oh_goon.core.models.fleet_plan import AgentStepRequestedEffects
from oh_goon.runtime.enforcer_lace_journal import (
    CausalJournalError,
    CausalRunJournal,
    _event_identity,
)
from tests.unit.runtime.test_ag02b2b1_original_agent_source_reservation import fixture, reserve
from tests.unit.runtime.test_skill03c_original_signed_agent_demand import EFFECT_STEP


def limit(**changes):
    base = {
        "repository_access": "write",
        "recipients": ["internal_model", "secondary_model"],
        "capabilities": {
            "tools": ["git", "pytest", "ruff"],
            "egress": {
                "mode": "cidr_allowlist",
                "rules": [
                    {"cidr": "0.0.0.0/0", "protocol": "tcp", "ports": [443, 8443]},
                ],
            },
            "nested_strict_execution": False,
        },
    }
    base.update(changes)
    return AgentStepRequestedEffects.model_validate(base).model_dump(mode="json")


def reject_before_write(conn, journal, args):
    before = tuple(conn.execute("SELECT event_payload FROM lace_run_events ORDER BY run_seq"))
    with pytest.raises(CausalJournalError, match="independently signed effect limits"):
        reserve(conn, journal, args)
    assert (
        tuple(conn.execute("SELECT event_payload FROM lace_run_events ORDER BY run_seq")) == before
    )


def test_original_signed_ceiling_can_be_stricter_than_issuer_upper_bound_and_never_grants(tmp_path):
    ceiling = limit()
    conn, journal, args, packet = fixture(tmp_path, effect_step=EFFECT_STEP, effect_limits=ceiling)
    assert reserve(conn, journal, args) == 3
    binding = journal.verify("run-one", auth=args["auth"])[-1]["payload"]["binding"]
    assert binding["effect_limits_identity"] == digest(
        "oh-goon/agent-enforcer-effect-limits/v1", canonical_bytes(ceiling)
    )
    assert binding["requested_effects_identity"] != binding["effect_limits_identity"]
    assert binding["effect_permission"] == "NONE"
    assert binding["authority"] == "PREWORK_NOT_ADMITTED"
    assert "agent_effect_limits" not in binding
    assert "secondary_model" not in str(binding)
    conn.close()
    reopened = sqlite3.connect(tmp_path / "authority.sqlite", isolation_level=None)
    assert (
        CausalRunJournal(reopened).verify("run-one", auth=args["auth"])[-1]["payload"]["binding"]
        == binding
    )
    reopened.close()


def test_independent_ceiling_absent_denies_even_identical_original_request(tmp_path):
    conn, journal, args, _ = fixture(tmp_path, effect_step=EFFECT_STEP, effect_limits="missing")
    reject_before_write(conn, journal, args)
    conn.close()


@pytest.mark.parametrize(
    "limits",
    [
        limit(repository_access="read_only"),
        limit(recipients=["secondary_model"]),
        limit(
            capabilities={
                "tools": ["git", "ruff"],
                "egress": {
                    "mode": "cidr_allowlist",
                    "rules": [
                        {"cidr": "0.0.0.0/0", "protocol": "tcp", "ports": [443, 8443]},
                    ],
                },
            }
        ),
        limit(capabilities={"tools": ["git", "pytest", "ruff"], "egress": {"mode": "none"}}),
        limit(
            capabilities={
                "tools": ["git", "pytest", "ruff"],
                "egress": {
                    "mode": "cidr_allowlist",
                    "rules": [
                        {"cidr": "192.168.0.0/16", "protocol": "tcp", "ports": [443]},
                    ],
                },
            }
        ),
        limit(
            capabilities={
                "tools": ["git", "pytest", "ruff"],
                "egress": {
                    "mode": "cidr_allowlist",
                    "rules": [
                        {"cidr": "0.0.0.0/0", "protocol": "udp", "ports": [443]},
                    ],
                },
            }
        ),
        limit(
            capabilities={
                "tools": ["git", "pytest", "ruff"],
                "egress": {
                    "mode": "cidr_allowlist",
                    "rules": [
                        {"cidr": "0.0.0.0/0", "protocol": "tcp", "ports": [8443]},
                    ],
                },
            }
        ),
    ],
)
def test_any_effect_outside_independently_signed_ceiling_refuses_before_mutation(tmp_path, limits):
    conn, journal, args, _ = fixture(tmp_path, effect_step=EFFECT_STEP, effect_limits=limits)
    reject_before_write(conn, journal, args)
    conn.close()


@pytest.mark.parametrize(
    "malformed",
    [
        {"repository_access": "write"},
        {"repository_access": "admin", "recipients": [], "capabilities": {}},
        {
            "repository_access": "write",
            "recipients": ["internal_model", "internal_model"],
            "capabilities": {},
        },
        {
            "repository_access": "write",
            "recipients": [],
            "capabilities": {"egress": {"mode": "host_unrestricted"}},
        },
    ],
)
def test_signed_ceiling_must_be_fully_typed_and_canonical(tmp_path, malformed):
    conn, journal, args, _ = fixture(tmp_path, effect_step=EFFECT_STEP, effect_limits=malformed)
    reject_before_write(conn, journal, args)
    conn.close()


def test_ceiling_event_identity_unauthorized_rewrite_rejected_even_with_valid_test_issuer(tmp_path):
    conn, journal, args, _ = fixture(tmp_path, effect_step=EFFECT_STEP, effect_limits=limit())
    assert reserve(conn, journal, args) == 3
    before = conn.execute(
        "SELECT event_payload,previous_identity FROM lace_run_events WHERE run_seq=3"
    ).fetchone()
    item = json.loads(before[0])
    item["binding"]["effect_limits_identity"] = "sha256:" + "f" * 64
    # Authorized TEST signing key deliberately present; semantic check must
    # still reject a receipt that diverges from the separately signed Run.
    item["signature"] = args["auth"].issue("agent-source-prework", item["binding"]).mac_hex
    encoded = canonical_bytes(item)
    identity = _event_identity("run-one", 3, "AGENT_SOURCE_PREWORK_RESERVED", encoded, before[1])
    conn.execute(
        "UPDATE lace_run_events SET event_payload=?,event_identity=? WHERE run_seq=3",
        (encoded, identity),
    )
    conn.execute(
        "UPDATE lace_run_authority SET head_identity=? WHERE run_id=?", (identity, "run-one")
    )
    with pytest.raises(CausalJournalError, match="effect ceiling identity"):
        journal.verify("run-one", auth=args["auth"])
    conn.close()


def test_legacy_zero_request_encoding_remains_unmodified(tmp_path):
    conn, journal, args, _ = fixture(tmp_path)
    assert reserve(conn, journal, args) == 3
    original = journal.verify("run-one", auth=args["auth"])[-1]["payload"]["binding"]
    assert "effect_limits_identity" not in original
    assert "requested_effects_identity" not in original
    assert original["effect_permission"] == "NONE"
    conn.close()


def test_ceiling_cannot_be_supplied_as_a_separate_agent_permission_or_event_type():
    from oh_goon.repository_tooling.architecture.repository_layout import ROOT

    source = (ROOT / "backend/src/oh_goon/runtime/enforcer_lace_journal.py").read_text()
    assert source.count("def _independently_scoped_agent_effects(") == 1
    assert (
        source.count("_independently_scoped_agent_effects(") == 5
    )  # definition, prework, readback, tool cut, signed grant readback
    assert "AGENT_EFFECT_GRANTED" not in source
