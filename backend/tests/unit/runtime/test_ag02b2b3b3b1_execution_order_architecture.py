"""X0 regressions: execution order belongs to one original RESOURCE_INTENT writer.

These source-mutation tests fail when a future change quietly shifts authority,
adds a second writer, makes the predecessor check conditional, or moves it
past a physical-effect reservation. They do not certify native containment.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_original_resource_intent_sequence,
)
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from tests.unit.runtime.test_ag02b2b1_original_agent_source_reservation import fixture
from tests.unit.runtime.test_b12_local_resource_intent import _resources

JOURNAL = "backend/src/oh_goon/runtime/enforcer_lace_journal.py"


@pytest.fixture
def candidate(tmp_path: Path) -> Path:
    path = tmp_path / JOURNAL
    path.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / JOURNAL, path)
    assert _check_original_resource_intent_sequence(tmp_path / "backend/src/oh_goon") == []
    return path


def test_agent_admitted_step_is_blocked_even_before_prework_exists(tmp_path: Path) -> None:
    conn, journal, args, _packet = fixture(tmp_path)
    events_before = journal.verify("run-one", auth=args["auth"])
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
    assert journal.verify("run-one", auth=args["auth"]) == events_before
    conn.close()


@pytest.mark.parametrize(
    "old,new",
    [
        (
            "            _require_original_resource_intent_step_order(\n",
            "            # _require_original_resource_intent_step_order(\n",
        ),
        (
            "            _require_original_resource_intent_step_order(\n",
            "            if False:\n                _require_original_resource_intent_step_order(\n",
        ),
        ("original_events=original_events,", "original_events=[],"),
        ('step_envelope=fields["step_envelopes"][step_id],', "step_envelope={},"),
        ('"AGENT_SOURCE_PREWORK_STAGED"', '"NOT_THE_STAGE"'),
        (
            'step_envelope.get("workload_kind") == "agent"',
            'step_envelope.get("workload_kind") == "command"',
        ),
    ],
)
def test_mutating_canonical_predecessor_gate_fails_x0(candidate: Path, old: str, new: str) -> None:
    src = candidate.read_text()
    assert old in src
    candidate.write_text(src.replace(old, new, 1))
    assert _check_original_resource_intent_sequence(candidate.parents[1])


def test_reordering_predecessor_below_append_fails_x0(candidate: Path) -> None:
    src = candidate.read_text()
    beg = src.index("            _require_original_resource_intent_step_order(\n")
    end = src.index("\n            existing = self.connection.execute(", beg)
    guard = src[beg:end]
    src = src[:beg] + src[end:]
    anchor = '            seq = self._append_locked(run_id, "RESOURCE_INTENT", payload)\n'
    assert anchor in src
    candidate.write_text(src.replace(anchor, anchor + guard + "\n", 1))
    assert _check_original_resource_intent_sequence(candidate.parents[1])


def test_parallel_resource_intent_writer_fails_x0(candidate: Path) -> None:
    src = candidate.read_text()
    # Duplicate original named publisher in the original class: disallowed.
    first = src.index("    def publish_local_resource_intent_in(")
    second = src.index("    def publish_local_resource_intent(", first)
    duplicate = src[first:second]
    candidate.write_text(src[:second] + duplicate + src[second:])
    assert _check_original_resource_intent_sequence(candidate.parents[1])


def test_second_module_cannot_publish_original_resource_intent(candidate: Path) -> None:
    package = candidate.parents[1]
    rogue = package / "integrations" / "parallel_agent_intent.py"
    rogue.parent.mkdir(parents=True, exist_ok=True)
    rogue.write_text(
        'def bypass(journal, run):\n    journal._append_locked(run, "RESOURCE_INTENT", {})\n'
    )
    assert any(
        "parallel RESOURCE_INTENT" in v.message
        for v in _check_original_resource_intent_sequence(package)
    )
