"""M148 isolated future Enforcer/LACE causal journal; NOT wired to execution.

This protocol is an inactive admission/journal foundation; Phase 4 must replace
legacy admission publication atomically rather than run both protocols in parallel.
The supplied SQLite connection is the only transaction owner in these methods.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from oh_goon.runtime.database import LocalDatabase

from oh_goon.core.enforcer_lace_semantics import CanonicalOutcome, FactKind
from oh_goon.core.enforcer_lace_trust import (
    PROTOCOL_VERSION, AdmittedRunContract, AuthenticatedReceipt, ReceiptAuthenticator,
    TrustProtocolError, canonical_bytes, digest,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS lace_run_authority (
    run_id TEXT PRIMARY KEY, contract_identity TEXT NOT NULL,
    control_epoch INTEGER NOT NULL CHECK (control_epoch > 0),
    lease_identity TEXT NOT NULL, head_seq INTEGER NOT NULL, head_identity TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('ACTIVE','SEALED','UNRESOLVED')),
    closure_receipt BLOB, closure_mac TEXT, closure_key_id TEXT,
    closure_domain TEXT,
    admitted_contract BLOB NOT NULL, admission_receipt BLOB NOT NULL,
    admission_mac TEXT NOT NULL, admission_key_id TEXT NOT NULL,
    admission_domain TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lace_run_events (
    run_id TEXT NOT NULL, run_seq INTEGER NOT NULL CHECK (run_seq > 0),
    event_kind TEXT NOT NULL, event_payload BLOB NOT NULL,
    previous_identity TEXT NOT NULL, event_identity TEXT NOT NULL,
    PRIMARY KEY (run_id, run_seq),
    FOREIGN KEY (run_id) REFERENCES lace_run_authority(run_id)
);
"""
GENESIS = digest("oh-goon/run-journal-genesis/v1", b"")

# Group B local resource reservations.  These are immutable pre-work labels, NOT
# proof that Bubblewrap resources were created, isolated or torn down.
_LOCAL_RESOURCE_KEYS = frozenset({"workspace", "runtime", "network", "cgroup", "mounts"})


def _require_digest(value: str, *, name: str) -> None:
    if (type(value) is not str or not value.startswith("sha256:")
            or len(value) != 71 or any(char not in "0123456789abcdef" for char in value[7:])):
        raise CausalJournalError(f"local LACE {name} requires canonical SHA-256 identity")



def _require_original_resource_intent_step_order(
    *, step_id: str, attempt_identity: str, step_envelope: object,
    original_events: list[dict[str, Any]],
) -> None:
    """One original pre-effect transition check for every workload kind.

    Command workload follows its existing path. Agent source prework is NOT
    mount permission, regardless of the caller or mount category. This same
    original RESOURCE_INTENT owner must be extended with authenticated
    stage-to-mount custody before enabling an Agent Step; no side gate can
    grant it. Neither a missing prework event nor forged command mounts can
    bypass the admitted workload kind.
    """
    if not original_events or original_events[0]["kind"] != "RUN_ADMITTED":
        raise CausalJournalError("original Run admission must precede resource intent")
    if (type(step_envelope) is dict
            and step_envelope.get("workload_kind") == "agent"):
        raise CausalJournalError(
            "Agent Step has no qualified signed RESOURCE_INTENT mount custody"
        )
    if any(
        event["kind"] in {"AGENT_SOURCE_PREWORK_RESERVED", "AGENT_SOURCE_PREWORK_STAGED"}
        and event["payload"]["binding"].get("step_id") == step_id
        for event in original_events
    ):
        raise CausalJournalError(
            "Agent Step source prework has no qualified signed RESOURCE_INTENT mount custody"
        )


def _independently_scoped_agent_effects(
    requested: dict[str, Any] | None, signed_step: dict[str, Any],
) -> str | None:
    """Narrow original AG-02 demand by independently issuer-bound Enforcer limits.

    This is an ADMISSION CEILING, not a Step effect grant. Original request
    cannot supply or widen the ceiling. Network comparison is conservative:
    CIDR destinations/ports must be contained; named destinations must match
    by exact name/protocol with subset ports. Cross-mode upgrades fail.
    """
    from ipaddress import ip_network
    from pydantic import ValidationError
    from oh_goon.core.models.fleet_plan import AgentStepRequestedEffects

    try:
        request = (AgentStepRequestedEffects.model_validate(requested)
                   if requested is not None else AgentStepRequestedEffects())
        if requested is not None and request.model_dump(mode="json") != requested:
            raise ValueError("noncanonical requested Agent effects")
        ceiling_source = signed_step.get("agent_effect_limits")
        if ceiling_source is None:
            if request != AgentStepRequestedEffects() or "agent_effect_limits" in signed_step:
                raise ValueError("independent Agent effect limits missing")
            return None  # unchanged legacy zero-demand packet and event
        if type(ceiling_source) is not dict:
            raise ValueError("typed Agent effect limits required")
        ceiling = AgentStepRequestedEffects.model_validate(ceiling_source)
        if ceiling.model_dump(mode="json") != ceiling_source:
            raise ValueError("noncanonical Agent effect limits")
        if request.repository_access == "write" and ceiling.repository_access != "write":
            raise ValueError("repository write outside independent limits")
        if not set(request.recipients).issubset(ceiling.recipients):
            raise ValueError("model recipient outside independent limits")
        if not set(request.capabilities.tools).issubset(ceiling.capabilities.tools):
            raise ValueError("tool outside independent limits")
        demand_egress, admitted_egress = request.capabilities.egress, ceiling.capabilities.egress
        if demand_egress.mode != "none":
            if demand_egress.mode != admitted_egress.mode:
                raise ValueError("egress mode outside independent limits")
            for rule in demand_egress.rules:
                if demand_egress.mode == "cidr_allowlist":
                    accepted = any(
                        rule.protocol == allow.protocol
                        and ip_network(rule.cidr).subnet_of(ip_network(allow.cidr))
                        and set(rule.ports).issubset(allow.ports)
                        for allow in admitted_egress.rules
                    )
                else:
                    accepted = any(
                        rule.hostname == allow.hostname
                        and rule.protocol == allow.protocol
                        and set(rule.ports).issubset(allow.ports)
                        for allow in admitted_egress.rules
                    )
                if not accepted:
                    raise ValueError("egress destination outside independent limits")
    except (ValueError, ValidationError, TypeError, AttributeError) as exc:
        raise CausalJournalError("Agent request exceeds original independently signed effect limits") from exc
    return digest("oh-goon/agent-enforcer-effect-limits/v1", canonical_bytes(ceiling_source))


class CausalJournalError(TrustProtocolError):
    """Unverifiable durable Run authority; never proceed to physical work."""


def _event_identity(run_id: str, seq: int, kind: str, payload: bytes, previous: str) -> str:
    return digest("oh-goon/causal-journal-event/v1", canonical_bytes({
        "run_id": run_id, "run_seq": seq, "kind": kind,
        "payload_identity": digest("oh-goon/journal-payload/v1", payload),
        "previous_identity": previous,
    }))


class CausalRunJournal:
    """Single pre-live authority for atomic admit/append/fence/terminal protocol."""

    def __init__(self, connection: sqlite3.Connection, *, owner: LocalDatabase | None = None) -> None:
        self.connection = connection
        self._database_owner = owner
        self.connection.execute("PRAGMA foreign_keys = ON")

    def initialize_schema(self) -> None:
        """Explicit opt-in, never initialized by production startup in M148."""
        self.connection.executescript(SCHEMA)

    def _transaction(self, action: Callable[[], Any]) -> Any:
        """DB1B2: standalone pre-live transactions are not production authority.

        The historical raw SQLite fixture implementation lives only under
        tests/fixtures/db1b2_legacy_sqlite.py. Runtime callers must use the
        existing LocalDatabase owner and the in-transaction domain participants.
        """
        raise CausalJournalError("standalone causal transaction forbidden; use LocalDatabase owner")

    def publish_admission(self, contract: AdmittedRunContract, receipt: AuthenticatedReceipt,
                          auth: ReceiptAuthenticator, *, control_epoch: int,
                          lease_identity: str, before_commit: Callable[[], None] | None = None) -> None:
        self.publish_admission_with_operation(
            contract, receipt, auth, control_epoch=control_epoch,
            lease_identity=lease_identity,
            operational_admission=lambda _connection: None,
            before_commit=before_commit,
        )

    def publish_admission_with_operation(
        self, contract: AdmittedRunContract, receipt: AuthenticatedReceipt,
        auth: ReceiptAuthenticator, *, control_epoch: int, lease_identity: str,
        operational_admission: Callable[[sqlite3.Connection], Any],
        before_commit: Callable[[], None] | None = None,
    ) -> Any:
        """Atomically publish legacy operational authority and the causal Run cut.

        A single SQLite connection and BEGIN IMMEDIATE own the operational lease,
        operational RUN_ADMITTED, authenticated contract and causal RUN_ADMITTED.
        Any exception rolls *all* of these back. The callback must only mutate this
        connection; a second connection, external effect or nested commit is forbidden.
        This is an opt-in integration primitive, not a live executor entrypoint.
        """
        if self.connection.in_transaction:
            raise CausalJournalError("caller has outstanding transaction; use explicit in-transaction admission")
        return self._transaction(lambda: self.publish_admission_in(
            self.connection, contract, receipt, auth,
            control_epoch=control_epoch, lease_identity=lease_identity,
            operational_admission=operational_admission, before_commit=before_commit,
        ))

    def publish_admission_in(
        self, connection: sqlite3.Connection,
        contract: AdmittedRunContract, receipt: AuthenticatedReceipt,
        auth: ReceiptAuthenticator, *, control_epoch: int, lease_identity: str,
        operational_admission: Callable[[sqlite3.Connection], Any],
        before_commit: Callable[[], None] | None = None,
    ) -> Any:
        """M169: publish in the **original caller-owned** write transaction.

        Used by the Fleet execution reservation owner after it acquires the
        durable lease. Never begins, commits, rolls back, or opens a second
        transaction. The owner must roll back on any exception; this function
        cannot itself authorize a live entrypoint.
        """
        if connection is not self.connection or not connection.in_transaction:
            raise CausalJournalError("original active Fleet write transaction required")
        # A deferred/read-only SQLite transaction is not a valid admission owner.
        # This is an in-transaction check only; the caller owns BEGIN IMMEDIATE.
        contract.verify()
        fields = auth.verify(receipt, domain="admission")
        run_id = contract.fields["run_id"]
        if fields != {"decision": "ADMITTED", "run_id": run_id,
                      "contract_identity": contract.identity, "protocol": PROTOCOL_VERSION}:
            raise CausalJournalError("admission receipt does not bind exact Run contract")
        if type(control_epoch) is not int or control_epoch < 1 or not lease_identity:
            raise CausalJournalError("valid epoch and control lease required")

        if not callable(operational_admission):
            raise CausalJournalError("operational admission callback required")

        result = operational_admission(connection)
        if not connection.in_transaction:
            raise CausalJournalError("operational admission ended the authority transaction")
        connection.execute("""INSERT INTO lace_run_authority
                (run_id, contract_identity, control_epoch, lease_identity, head_seq, head_identity, state,
                 admitted_contract, admission_receipt, admission_mac, admission_key_id, admission_domain)
                VALUES (?, ?, ?, ?, 0, ?, 'ACTIVE', ?, ?, ?, ?, ?)""",
                (run_id, contract.identity, control_epoch, lease_identity, GENESIS,
                 contract.encoded, receipt.payload, receipt.mac_hex, receipt.key_id, receipt.domain))
        self._append_locked(run_id, "RUN_ADMITTED", {"contract_identity": contract.identity})
        if before_commit:
            before_commit()
        if not connection.in_transaction:
            raise CausalJournalError("admission fence ended the authority transaction")
        return result

    def _row(self, run_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT contract_identity, control_epoch, lease_identity, head_seq, head_identity, state FROM lace_run_authority WHERE run_id=?",
            (run_id,)).fetchone()
        if row is None:
            raise CausalJournalError("unknown or unpublished Run")
        return row

    def _check_fence(self, row: sqlite3.Row, *, epoch: int, lease_identity: str) -> None:
        if type(epoch) is not int or row[1] != epoch or row[2] != lease_identity or not lease_identity:
            raise CausalJournalError("stale control epoch or lease")
        if row[5] != "ACTIVE":
            raise CausalJournalError("journal not active")

    def _append_locked(self, run_id: str, kind: str, payload: dict[str, Any]) -> int:
        row = self._row(run_id)
        seq = row[3] + 1
        encoded = canonical_bytes(payload)
        identity = _event_identity(run_id, seq, kind, encoded, row[4])
        self.connection.execute("""INSERT INTO lace_run_events
            (run_id,run_seq,event_kind,event_payload,previous_identity,event_identity)
            VALUES (?,?,?,?,?,?)""", (run_id, seq, kind, encoded, row[4], identity))
        self.connection.execute("UPDATE lace_run_authority SET head_seq=?, head_identity=? WHERE run_id=?",
                                (seq, identity, run_id))
        return seq

    def append(self, run_id: str, *, epoch: int, lease_identity: str,
               kind: str, payload: dict[str, Any], before_commit: Callable[[], None] | None = None) -> int:
        allowed = {member.value for member in FactKind} | {
            "STEP_OUTPUT_RECEIPT",
            "EVALUATOR_DECISION", "RUN_CONTROL_FENCED",
        }
        if kind not in allowed or kind == "RUN_ADMITTED":
            raise CausalJournalError("reserved or unknown causal journal event")
        def commit() -> int:
            self._check_fence(self._row(run_id), epoch=epoch, lease_identity=lease_identity)
            seq = self._append_locked(run_id, kind, payload)
            if before_commit:
                before_commit()
            return seq
        return self._transaction(commit)

    def reserve_agent_source_prework_in(
        self, run_id: str, *, epoch: int, lease_identity: str,
        auth: ReceiptAuthenticator, signer_grant: "SignerTrustGrant",
        step_id: str, attempt_identity: str, executor_generation: int,
        packet: bytes, expected_handoff_identity: str,
        requested_effects: dict[str, Any] | None = None,
    ) -> int:
        """AG-02B2B1: append an authenticated *non-executable* packet commitment.

        Only the original admitted Fleet SQLite write owner can call this. Source
        bytes are verified in-memory then discarded; they are NEVER journaled.
        This is a prework custody *commitment*, not a source delivery, a Step
        reservation, permission to disclose data or a launcher. The admitted
        Step-envelope requirement deliberately prevents production Agent Steps
        from reaching here until original admission supports the typed binding.
        """
        from oh_goon.runtime.enforcer_lace_signer_trust_epoch import (
            SignerTrustEpochAuthority, SignerTrustGrant,
        )
        from oh_goon.runtime.enforcer_lace_admitted_signer_epoch import (
            require_original_admitted_signer_epoch_in,
        )

        if (not self.connection.in_transaction or type(self) is not CausalRunJournal
                or type(auth) is not ReceiptAuthenticator
                or type(signer_grant) is not SignerTrustGrant
                or type(run_id) is not str or not run_id
                or type(step_id) is not str or not step_id
                or type(executor_generation) is not int or executor_generation < 1):
            raise CausalJournalError("original admitted Fleet agent prework transaction required")
        _require_digest(attempt_identity, name="agent attempt")
        self._check_fence(self._row(run_id), epoch=epoch, lease_identity=lease_identity)

        # The same original Fleet lease, not a caller-supplied selected scope,
        # cancellation-free and never a recovery-only custodian.
        try:
            lease = self.connection.execute("""SELECT lease_token, kind, fleet_run_id,
                cancel_requested_at, owner_session_id FROM execution_lease WHERE singleton=1""").fetchone()
        except sqlite3.Error as exc:
            raise CausalJournalError("original Fleet execution lease unavailable") from exc
        if (lease is None or lease[0] != lease_identity or lease[1] != "FLEET"
                or lease[2] != run_id or lease[3] is not None
                or type(lease[4]) is not str or not lease[4]
                or lease[4].startswith("recovery:")):
            raise CausalJournalError("original Fleet Step lease absent, cancelled or recovery-only")

        events = self.verify(run_id, auth=auth)
        if len(events) < 2 or events[1]["kind"] != "RUN_SIGNER_EPOCH_BOUND":
            raise CausalJournalError("signed original Run signer epoch required")
        if SignerTrustEpochAuthority(self.connection).active() != signer_grant:
            raise CausalJournalError("original active Fleet signer epoch changed or was revoked")
        require_original_admitted_signer_epoch_in(
            self, run_id=run_id, auth=auth, expected=signer_grant,
        )
        row = self._row(run_id)
        contract_bytes = self.connection.execute(
            "SELECT admitted_contract FROM lace_run_authority WHERE run_id=?", (run_id,),
        ).fetchone()[0]
        contract = AdmittedRunContract(contract_bytes, row[0])
        contract.verify()
        fields = contract.fields
        if (fields["executor_generation"] != executor_generation
                or step_id not in fields["step_teardown_envelopes"]):
            raise CausalJournalError("agent source diverges from original admitted Run/Step generation")
        step_envelope = fields["step_envelopes"].get(step_id)
        if (type(step_envelope) is not dict
                or step_envelope.get("workload_kind") != "agent"
                or step_envelope.get("agent_handoff_identity") != expected_handoff_identity):
            raise CausalJournalError("typed Agent Step identity absent from original admission")
        # SKILL-3C: the original signed Run/Step contract must commit to exactly
        # the demand obtained from the *original* AG-02 source decoder. A Plan
        # request cannot grant itself tools, write access, or disclosure. This
        # prework correlation deliberately leaves effect_permission = NONE.
        envelope_demand = step_envelope.get("agent_requested_effects")
        if requested_effects is None:
            if envelope_demand is not None or "agent_requested_effects" in step_envelope:
                raise CausalJournalError("Agent Step demand differs from admitted Run envelope")
        elif (type(requested_effects) is not dict or type(envelope_demand) is not dict
              or canonical_bytes(requested_effects) != canonical_bytes(envelope_demand)):
            raise CausalJournalError("Agent Step demand differs from admitted Run envelope")
        # SKILL-3C2A: a request matching itself is insufficient. Only a
        # separately authored, issuer-bound upper bound in the SAME original
        # signed Run/Step envelope can accept nondefault effect demands.
        limits_identity = _independently_scoped_agent_effects(requested_effects, step_envelope)
        # AG-02B2A source validation occurs in the original service caller,
        # above this journal/runtime boundary. The journal never parses source.
        if (type(packet) is not bytes or not packet
                or type(expected_handoff_identity) is not str
                or not expected_handoff_identity.startswith("agent-step-prework-handoff-v1-")
                or len(expected_handoff_identity) != len("agent-step-prework-handoff-v1-") + 64):
            raise CausalJournalError("exact immutable agent source commitment required")
        for event in events:
            if event["kind"] == "AGENT_SOURCE_PREWORK_RESERVED" and (
                event["payload"]["binding"].get("step_id") == step_id
            ):
                raise CausalJournalError("original Agent Step source already reserved; replay denied")
        binding = {
            "protocol": PROTOCOL_VERSION, "run_id": run_id,
            "contract_identity": contract.identity, "step_id": step_id,
            "attempt_identity": attempt_identity,
            "control_epoch": epoch, "lease_identity": lease_identity,
            "executor_generation": executor_generation,
            "signer_trust_epoch": signer_grant.epoch,
            "handoff_identity": expected_handoff_identity,
            "packet_identity": digest("oh-goon/agent-source-prework-packet/v1", packet),
            "packet_bytes": len(packet),
            "authority": "PREWORK_NOT_ADMITTED", "effect_permission": "NONE",
        }
        if requested_effects is not None:
            binding["requested_effects_identity"] = digest(
                "oh-goon/agent-requested-effects/v1", canonical_bytes(requested_effects),
            )
        if limits_identity is not None:
            binding["effect_limits_identity"] = limits_identity
        signed = auth.issue("agent-source-prework", binding)
        return self._append_locked(run_id, "AGENT_SOURCE_PREWORK_RESERVED", {
            "binding": binding, "signature": signed.mac_hex,
            "signature_domain": signed.domain, "signature_key_id": signed.key_id,
        })

    def record_agent_prework_stage_in(
        self, run_id: str, *, epoch: int, lease_identity: str,
        auth: ReceiptAuthenticator, reservation_seq: int,
        step_id: str, attempt_identity: str, stage: dict[str, Any],
        source_mount: "OriginalSandboxMount | None" = None,
    ) -> int:
        """Record readback and optional mount proposal in the SAME signed stage.

        Only callable by the original Fleet transaction owner after the existing
        attempt's private filesystem write. No physical sandbox or execution
        permission is conferred. A crash before commit leaves no signed stage.
        """
        if not self.connection.in_transaction or type(self) is not CausalRunJournal:
            raise CausalJournalError("original Fleet staging write transaction required")
        self._check_fence(self._row(run_id), epoch=epoch, lease_identity=lease_identity)
        events = self.verify(run_id, auth=auth)
        reserved = [e for e in events if e["kind"] == "AGENT_SOURCE_PREWORK_RESERVED"
                    and e["run_seq"] == reservation_seq]
        if len(reserved) != 1:
            raise CausalJournalError("Agent staging requires exact original source reservation")
        previous = reserved[0]["payload"]["binding"]
        if (previous["step_id"] != step_id or previous["attempt_identity"] != attempt_identity
                or previous["control_epoch"] != epoch or previous["lease_identity"] != lease_identity):
            raise CausalJournalError("Agent staged source diverges from original attempt")
        required = {"schema", "handoff_identity", "stage_identity", "file_count",
                    "readback_verified", "effect_permission", "physical_sandbox_proven"}
        if (type(stage) is not dict or set(stage) != required
                or stage["schema"] != "oh-goon.agent-prework-attempt-stage/v1"
                or stage["handoff_identity"] != previous["handoff_identity"]
                or type(stage["file_count"]) is not int or stage["file_count"] < 2
                or stage["readback_verified"] is not True or stage["effect_permission"] != "NONE"
                or stage["physical_sandbox_proven"] is not False):
            raise CausalJournalError("Agent prework stage is incomplete or claims effects")
        _require_digest(stage["stage_identity"], name="Agent stage")
        if any(e["kind"] == "AGENT_SOURCE_PREWORK_STAGED" and
               e["payload"]["binding"]["reservation_seq"] == reservation_seq for e in events):
            raise CausalJournalError("original Agent stage already recorded")
        binding = {"run_id": run_id, "step_id": step_id,
                   "attempt_identity": attempt_identity, "reservation_seq": reservation_seq,
                   "contract_identity": previous["contract_identity"], "stage": dict(stage),
                   "effect_permission": "NONE", "physical_sandbox_proven": False}
        if source_mount is not None:
            # The existing attempt owner supplies this verified proposal. It is
            # signed as part of the ORIGINAL stage event, never as a new grant.
            from oh_goon.core.ports.original_mount_intent import OriginalSandboxMount
            if type(source_mount) is not OriginalSandboxMount or source_mount.target != "/opt/oh-goon/agent-source":
                raise CausalJournalError("Agent staged source mount must use original fixed sandbox target")
            binding["source_mount"] = {
                "source": source_mount.source, "target": source_mount.target,
                "sha256": source_mount.sha256,
            }
        signed = auth.issue("agent-source-prework-stage", binding)
        return self._append_locked(run_id, "AGENT_SOURCE_PREWORK_STAGED", {
            "binding": binding, "signature": signed.mac_hex,
            "signature_domain": signed.domain, "signature_key_id": signed.key_id,
        })

    def inspect_original_agent_tool_launch_scope_in(
        self, run_id: str, *, epoch: int, lease_identity: str,
        auth: ReceiptAuthenticator, step_id: str,
        operational_attempt_identity: str, operational_attempt_root: "Path",
        argv: tuple[str, ...], mounted_tool_names: tuple[str, ...],
        actual_egress: dict[str, Any], read_only_workspace: bool,
    ) -> None:
        """Inspect one real pre-allocation tool effect in the ORIGINAL Fleet cut.

        A scope match does NOT authorize execution. The existing signed Agent
        RESOURCE_INTENT prohibition remains mandatory until LACE independently
        proves staged-source mount custody, execution and teardown. No separate
        permission registry, signer or process launcher is introduced.
        """
        from pathlib import Path
        from oh_goon.core.models.fleet_plan import AgentStepRequestedEffects
        from oh_goon.core.path_layouts import SANDBOX_PATHS

        if not self.connection.in_transaction:
            raise CausalJournalError("original Fleet effect-time transaction required")
        _require_digest(operational_attempt_identity, name="operational Agent attempt")
        if (not isinstance(operational_attempt_root, Path)
                or not operational_attempt_root.is_absolute()):
            raise CausalJournalError("original Agent operational attempt root missing")
        row = self._row(run_id)
        self._check_fence(row, epoch=epoch, lease_identity=lease_identity)
        events = self.verify(run_id, auth=auth)
        encoded_contract = self.connection.execute(
            "SELECT admitted_contract FROM lace_run_authority WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        contract = AdmittedRunContract(encoded_contract, row[0])
        contract.verify()
        signed_step = contract.fields["step_envelopes"].get(step_id)
        if type(signed_step) is not dict:
            raise CausalJournalError("original signed Step missing at tool cut")
        if signed_step.get("workload_kind") != "agent":
            return  # Existing command Step authority remains unchanged.
        reserved = [e for e in events if e["kind"] == "AGENT_SOURCE_PREWORK_RESERVED"
                    and e["payload"]["binding"]["step_id"] == step_id]
        if len(reserved) != 1:
            raise CausalJournalError("Agent tool effect lacks original signed source prework")
        source = reserved[0]["payload"]["binding"]
        from oh_goon.runtime.enforcer_lace_signer_trust_epoch import SignerTrustEpochAuthority
        from oh_goon.runtime.enforcer_lace_admitted_signer_epoch import (
            require_original_admitted_signer_epoch_in,
        )
        grant = SignerTrustEpochAuthority(self.connection).active()
        require_original_admitted_signer_epoch_in(
            self, run_id=run_id, auth=auth, expected=grant,
        )
        if source["signer_trust_epoch"] != grant.epoch:
            raise CausalJournalError("Agent tool launch signer generation is stale")
        staged = [e for e in events if e["kind"] == "AGENT_SOURCE_PREWORK_STAGED"
                  and e["payload"]["binding"]["reservation_seq"] == reserved[0]["run_seq"]]
        if (len(staged) != 1 or source["control_epoch"] != epoch
                or source["lease_identity"] != lease_identity
                or source["contract_identity"] != contract.identity
                or source["executor_generation"] != contract.fields["executor_generation"]
                or source["effect_permission"] != "NONE"
                or staged[0]["payload"]["binding"]["effect_permission"] != "NONE"):
            raise CausalJournalError("Agent tool effect differs from original staged Run authority")
        # SKILL-3C2D2A: a previous source-prework receipt is not proof that
        # the proposed native launch belongs to that exact operational attempt.
        # The caller derives this identity from the ORIGINAL STEP_ATTEMPT_CREATED
        # event inside its Fleet session transaction, never from Agent input.
        stage_binding = staged[0]["payload"]["binding"]
        if (source["attempt_identity"] != operational_attempt_identity
                or stage_binding["attempt_identity"] != operational_attempt_identity):
            raise CausalJournalError("Agent source does not belong to operational Step attempt")
        mount = stage_binding.get("source_mount")
        if (type(mount) is not dict or set(mount) != {"source", "target", "sha256"}
                or mount["source"] != str(operational_attempt_root / "agent-source-prework")
                or mount["target"] != "/opt/oh-goon/agent-source"
                or type(mount["sha256"]) is not str
                or len(mount["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in mount["sha256"])):
            raise CausalJournalError("Agent source mount differs from operational Step attempt")
        requested_raw = signed_step.get("agent_requested_effects")
        if type(requested_raw) is not dict:
            raise CausalJournalError("Agent tool effect requires explicit signed demand")
        # Reuse the ONE existing Enforcer narrowing owner, rather than re-deciding
        # policy with a second launch-time rule implementation.
        if _independently_scoped_agent_effects(requested_raw, signed_step) is None:
            raise CausalJournalError("Agent tool effect lacks an independent ceiling")
        request = AgentStepRequestedEffects.model_validate(requested_raw)
        # This first cut covers only read-only, no-egress, no-disclosure tools.
        # Writes, recipient disclosures and network require their own actual
        # physical/broker enforcement before they can become executable.
        if (request.repository_access != "read_only" or request.recipients
                or request.capabilities.egress.mode != "none"
                or type(actual_egress) is not dict
                or actual_egress != request.capabilities.egress.model_dump(mode="json")
                or read_only_workspace is not True):
            raise CausalJournalError("Agent tool effect has an unenforced write, disclosure or egress scope")
        permitted = set(request.capabilities.tools)
        if (type(argv) is not tuple or not argv or not all(type(a) is str for a in argv)
                or type(mounted_tool_names) is not tuple
                or not mounted_tool_names
                or not all(type(t) is str for t in mounted_tool_names)
                or len(set(mounted_tool_names)) != len(mounted_tool_names)
                or not set(mounted_tool_names).issubset(permitted)
                or argv[0] not in {
                    str(SANDBOX_PATHS.toolchain / tool) for tool in mounted_tool_names
                }):
            raise CausalJournalError("Agent tool launch differs from signed explicit tool scope")
        # Validation only. There is intentionally no returned grant or journal
        # event; the existing original RESOURCE_INTENT still denies Agent Steps.

    def issue_original_agent_tool_launch_grant_in(
        self, run_id: str, *, epoch: int, lease_identity: str,
        auth: ReceiptAuthenticator, step_id: str,
        operational_attempt_identity: str, operational_attempt_root: "Path",
        attempt_number: int, command_ordinal: int,
        argv: tuple[str, ...], mounted_tool_names: tuple[str, ...],
        actual_egress: dict[str, Any], read_only_workspace: bool,
    ) -> int:
        """One original Enforcer decision for an exact operational Agent tool call.

        This is a POSITIVE Enforcer scope grant, conditional on a subsequent
        original signed RESOURCE_INTENT and actual LACE custody. It cannot be
        consumed as standalone process/mount authority. The existing Agent
        RESOURCE_INTENT refusal is intentionally not changed here; therefore
        a production Agent reservation still rolls back this grant atomically.
        """
        if (type(self) is not CausalRunJournal or not self.connection.in_transaction
                or type(attempt_number) is not int or attempt_number < 1
                or type(command_ordinal) is not int or command_ordinal < 1):
            raise CausalJournalError("original Agent tool grant requires exact attempt and ordinal")
        if operational_attempt_root.name != f"{attempt_number:02d}":
            raise CausalJournalError("Agent tool grant attempt number differs from staged attempt")
        self.inspect_original_agent_tool_launch_scope_in(
            run_id, epoch=epoch, lease_identity=lease_identity, auth=auth,
            step_id=step_id, operational_attempt_identity=operational_attempt_identity,
            operational_attempt_root=operational_attempt_root,
            argv=argv, mounted_tool_names=mounted_tool_names,
            actual_egress=actual_egress, read_only_workspace=read_only_workspace,
        )
        original_events = self.verify(run_id, auth=auth)
        reserved = [e for e in original_events if e["kind"] == "AGENT_SOURCE_PREWORK_RESERVED"
                    and e["payload"]["binding"]["step_id"] == step_id]
        staged = [e for e in original_events if e["kind"] == "AGENT_SOURCE_PREWORK_STAGED"
                  and e["payload"]["binding"]["reservation_seq"] == reserved[0]["run_seq"]]
        if len(staged) != 1:
            raise CausalJournalError("original signed Agent stage required before grant")
        if any(e["kind"] == "AGENT_TOOL_LAUNCH_GRANTED"
               and e["payload"]["binding"]["step_id"] == step_id
               and e["payload"]["binding"]["attempt_identity"] == operational_attempt_identity
               and e["payload"]["binding"]["command_ordinal"] == command_ordinal
               for e in original_events):
            raise CausalJournalError("replayed original Agent tool grant")
        from oh_goon.runtime.enforcer_lace_signer_trust_epoch import SignerTrustEpochAuthority
        signer = SignerTrustEpochAuthority(self.connection).active()
        row = self._row(run_id)
        binding = {
            "protocol": PROTOCOL_VERSION, "run_id": run_id,
            "contract_identity": row[0], "step_id": step_id,
            "attempt_identity": operational_attempt_identity,
            "attempt_number": attempt_number, "command_ordinal": command_ordinal,
            "control_epoch": epoch, "lease_identity": lease_identity,
            "executor_generation": reserved[0]["payload"]["binding"]["executor_generation"],
            "signer_trust_epoch": signer.epoch,
            "source_reservation_seq": reserved[0]["run_seq"],
            "source_stage_seq": staged[0]["run_seq"],
            "requested_effects_identity": reserved[0]["payload"]["binding"].get("requested_effects_identity"),
            "effect_limits_identity": reserved[0]["payload"]["binding"].get("effect_limits_identity"),
            "tool_name": argv[0].removeprefix("/toolchain/bin/"),
            "command_identity": digest("oh-goon/agent-tool-launch-command/v1", canonical_bytes({
                "argv": list(argv), "mounted_tool_names": list(mounted_tool_names),
                "actual_egress": actual_egress, "read_only_workspace": read_only_workspace,
            })),
            "authority": "ENFORCER_GRANT_REQUIRES_LACE_RESOURCE_INTENT",
            "effect_permission": "NONE_UNTIL_LACE",
        }
        signed = auth.issue("agent-tool-launch-grant", binding)
        return self._append_locked(run_id, "AGENT_TOOL_LAUNCH_GRANTED", {
            "binding": binding, "signature": signed.mac_hex,
            "signature_domain": signed.domain, "signature_key_id": signed.key_id,
        })

    def original_agent_source_mount_for_grant_in(
        self, run_id: str, *, auth: ReceiptAuthenticator, grant_seq: int,
        step_id: str, operational_attempt_identity: str,
        operational_attempt_root: "Path", command_ordinal: int,
    ) -> "OriginalSandboxMount":
        """Resolve exact signed staged mount for one original conditional grant.

        Not a physical effect permit. The live native path still refuses Agent
        RESOURCE_INTENT and all Agent source mounts until LACE qualification.
        """
        from oh_goon.core.ports.original_mount_intent import OriginalSandboxMount
        if not self.connection.in_transaction:
            raise CausalJournalError('original Fleet transaction required for Agent mount projection')
        events = self.verify(run_id, auth=auth)
        matches = [e for e in events if e['run_seq'] == grant_seq
                   and e['kind'] == 'AGENT_TOOL_LAUNCH_GRANTED']
        if len(matches) != 1:
            raise CausalJournalError('original signed Agent tool grant missing')
        binding = matches[0]['payload']['binding']
        if (binding['step_id'] != step_id
                or binding['attempt_identity'] != operational_attempt_identity
                or binding['command_ordinal'] != command_ordinal
                or binding['effect_permission'] != 'NONE_UNTIL_LACE'):
            raise CausalJournalError('Agent source grant is not for this launch')
        sources = [e for e in events if e['run_seq'] == binding['source_stage_seq']
                   and e['kind'] == 'AGENT_SOURCE_PREWORK_STAGED']
        if len(sources) != 1:
            raise CausalJournalError('grant does not bind the original staged source')
        staged = sources[0]['payload']['binding']
        source_mount = staged.get('source_mount')
        if (staged['attempt_identity'] != operational_attempt_identity
                or type(source_mount) is not dict
                or set(source_mount) != {'source', 'target', 'sha256'}
                or source_mount['source'] != str(operational_attempt_root / 'agent-source-prework')
                or source_mount['target'] != '/opt/oh-goon/agent-source'):
            raise CausalJournalError('grant staged source differs from original attempt')
        return OriginalSandboxMount(**source_mount)

    def publish_local_resource_intent_in(
        self, run_id: str, *, epoch: int, lease_identity: str,
        auth: ReceiptAuthenticator, step_id: str, attempt_identity: str,
        execution_environment_identity: str, backend_capability_manifest_identity: str,
        resources: dict[str, str],
        before_commit: Callable[[], None] | None = None,
    ) -> int:
        """Append into the original owner's already-open admitted SQLite write cut."""
        if not self.connection.in_transaction:
            raise CausalJournalError("original admitted write transaction required")
        _require_digest(attempt_identity, name="attempt")
        _require_digest(execution_environment_identity, name="environment")
        _require_digest(backend_capability_manifest_identity, name="backend")
        if type(step_id) is not str or not step_id:
            raise CausalJournalError("local LACE requires exact Step identity")
        if type(resources) is not dict or set(resources) != _LOCAL_RESOURCE_KEYS:
            raise CausalJournalError("local LACE requires exact resource identity set")
        for name, identity in resources.items():
            _require_digest(identity, name=name)

        def commit() -> int:
            row = self._row(run_id)
            self._check_fence(row, epoch=epoch, lease_identity=lease_identity)
            original_events = self.verify(run_id, auth=auth)  # original signed history
            encoded_contract = self.connection.execute(
                "SELECT admitted_contract FROM lace_run_authority WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            contract = AdmittedRunContract(encoded_contract, row[0])
            contract.verify()
            fields = contract.fields
            if (fields["execution_environment_identity"] != execution_environment_identity
                    or fields["backend_capability_manifest_identity"] != backend_capability_manifest_identity
                    or step_id not in fields["step_envelopes"]
                    or step_id not in fields["step_teardown_envelopes"]):
                raise CausalJournalError("local LACE intent diverges from admitted Run/Step")
            # One canonical predecessor gate: original admitted Step kind +
            # authenticated journal history, inside this SAME write cut.
            _require_original_resource_intent_step_order(
                step_id=step_id, attempt_identity=attempt_identity,
                step_envelope=fields["step_envelopes"][step_id],
                original_events=original_events,
            )
            existing = self.connection.execute(
                "SELECT event_payload FROM lace_run_events WHERE run_id=? AND event_kind='RESOURCE_INTENT'",
                (run_id,),
            ).fetchall()
            for (raw,) in existing:
                payload = json.loads(raw)
                if payload.get("step_id") == step_id and payload.get("attempt_identity") == attempt_identity:
                    raise CausalJournalError("duplicate local LACE Step/attempt reservation")
            payload = {
                "backend": "bubblewrap", "step_id": step_id, "attempt_identity": attempt_identity,
                "contract_identity": contract.identity,
                "execution_environment_identity": execution_environment_identity,
                "backend_capability_manifest_identity": backend_capability_manifest_identity,
                "resources": dict(resources),
            }
            seq = self._append_locked(run_id, "RESOURCE_INTENT", payload)
            if before_commit:
                before_commit()
            return seq

        return commit()

    def publish_local_resource_intent(
        self, run_id: str, *, epoch: int, lease_identity: str,
        auth: ReceiptAuthenticator, step_id: str, attempt_identity: str,
        execution_environment_identity: str, backend_capability_manifest_identity: str,
        resources: dict[str, str],
        before_commit: Callable[[], None] | None = None,
    ) -> int:
        """Standalone development entrypoint; original Fleet uses the in-transaction owner."""
        return self._transaction(lambda: self.publish_local_resource_intent_in(
            run_id, epoch=epoch, lease_identity=lease_identity, auth=auth,
            step_id=step_id, attempt_identity=attempt_identity,
            execution_environment_identity=execution_environment_identity,
            backend_capability_manifest_identity=backend_capability_manifest_identity,
            resources=resources, before_commit=before_commit,
        ))

    def publish_local_allocation_observation_in(
        self, run_id: str, *, epoch: int, lease_identity: str,
        auth: ReceiptAuthenticator, step_id: str, attempt_identity: str,
        intent_run_seq: int, observation: dict[str, Any],
    ) -> int:
        """Append the bounded native pre-launch observation in the original write cut.

        Never confuses this event with process/mount execution evidence, terminal
        quiescence or independent physical Gate B attestation.
        """
        if not self.connection.in_transaction:
            raise CausalJournalError("original admitted write transaction required")
        _require_digest(attempt_identity, name="attempt")
        if type(intent_run_seq) is not int or intent_run_seq < 2:
            raise CausalJournalError("original committed local LACE intent sequence required")
        if type(step_id) is not str or not step_id:
            raise CausalJournalError("exact original Step identity required")
        required = {"resource_intent_identity", "cgroup_path", "cgroup_identity", "network_kind", "network_namespace",
                    "host_veth", "nft_table", "network_identity"}
        if type(observation) is not dict or set(observation) != required:
            raise CausalJournalError("exact native allocation observation required")
        _require_digest(observation["resource_intent_identity"], name="resource binding")
        _require_digest(observation["cgroup_identity"], name="observed cgroup")
        _require_digest(observation["network_identity"], name="observed network")
        if (type(observation["cgroup_path"]) is not str or not observation["cgroup_path"].startswith("/")
                or observation["network_kind"] not in {"not-allocated", "host-netns"}):
            raise CausalJournalError("invalid local LACE physical owner identity")
        net_fields = (observation["network_namespace"], observation["host_veth"], observation["nft_table"])
        if observation["network_kind"] == "not-allocated":
            if any(item is not None for item in net_fields):
                raise CausalJournalError("unallocated network contains invented resources")
        elif any(type(item) is not str or not item for item in net_fields):
            raise CausalJournalError("allocated network requires exact resource names")
        row = self._row(run_id)
        self._check_fence(row, epoch=epoch, lease_identity=lease_identity)
        events = self.verify(run_id, auth=auth)
        match = next((event for event in events if event["run_seq"] == intent_run_seq), None)
        if (match is None or match["kind"] != "RESOURCE_INTENT"
                or match["payload"].get("step_id") != step_id
                or match["payload"].get("attempt_identity") != attempt_identity):
            raise CausalJournalError("native observation does not bind the original intent")
        if observation["resource_intent_identity"] != digest(
            "oh-goon/b12d/resource-binding/v1", canonical_bytes(match["payload"]["resources"])
        ):
            raise CausalJournalError("native observed allocation diverges from original RESOURCE_INTENT")
        if any(event["kind"] == "RESOURCE_ALLOCATION_OBSERVED" and
               event["payload"].get("intent_run_seq") == intent_run_seq for event in events):
            raise CausalJournalError("duplicate local LACE allocation observation")
        return self._append_locked(run_id, "RESOURCE_ALLOCATION_OBSERVED", {
            "intent_run_seq": intent_run_seq, "step_id": step_id,
            "attempt_identity": attempt_identity, "observation": dict(observation),
            "stage": "PRE_LAUNCH_ONLY", "process_tree_proven": False,
            "bubblewrap_mounts_proven": False, "teardown_proven": False,
        })

    def claim_local_resource_recovery_in(
        self, run_id: str, *, epoch: int, lease_identity: str,
        auth: ReceiptAuthenticator, intent_run_seq: int,
    ) -> int:
        """Record an authenticated *cleanup-only* recovery claim after a crash.

        This records a recovery obligation, NOT physical rediscovery, cleanup,
        process/mount absence, or authority to resume a Step. Original Fleet
        recovery must invoke this inside its own session-owned write transaction.
        Later physical cleanup must re-fence the same epoch and lease; merely
        possessing this claim never authorizes a native delete.
        """
        if not self.connection.in_transaction:
            raise CausalJournalError("original recovery write transaction required")
        if type(intent_run_seq) is not int or intent_run_seq < 2:
            raise CausalJournalError("recovery requires original committed LACE intent sequence")
        self._check_fence(self._row(run_id), epoch=epoch, lease_identity=lease_identity)
        events = self.verify(run_id, auth=auth)
        source = next((event for event in events if event["run_seq"] == intent_run_seq), None)
        if source is None or source["kind"] != "RESOURCE_INTENT":
            raise CausalJournalError("recovery must reference an authenticated original RESOURCE_INTENT")
        payload = source["payload"]
        if (type(payload) is not dict or payload.get("backend") != "bubblewrap"
                or type(payload.get("step_id")) is not str or not payload["step_id"]):
            raise CausalJournalError("invalid original local LACE recovery subject")
        _require_digest(payload.get("attempt_identity"), name="recovery attempt")
        observed = [event for event in events if event["kind"] == "RESOURCE_ALLOCATION_OBSERVED"
                    and event["payload"].get("intent_run_seq") == intent_run_seq]
        if len(observed) > 1:
            raise CausalJournalError("ambiguous native allocation history")
        if observed:
            original = observed[0]["payload"]
            if (original.get("step_id") != payload["step_id"]
                    or original.get("attempt_identity") != payload["attempt_identity"]
                    or original.get("stage") != "PRE_LAUNCH_ONLY"):
                raise CausalJournalError("native allocation and original recovery intent disagree")
            stage = "PRELAUNCH_OBSERVED_NOT_QUIESCENT"
            allocation_seq: int | None = observed[0]["run_seq"]
            allocation_identity: str | None = digest(
                "oh-goon/b12d2/recovery-allocation/v1", canonical_bytes(original["observation"])
            )
        else:
            stage = "INTENT_ONLY_RESOURCE_EXISTENCE_UNKNOWN"
            allocation_seq = None
            allocation_identity = None
        prior = [event for event in events if event["kind"] == "RESOURCE_RECOVERY_CLAIMED"
                 and event["payload"].get("intent_run_seq") == intent_run_seq]
        if any(type(event["payload"].get("control_epoch")) is not int for event in prior):
            raise CausalJournalError("unverifiable recovery claim history")
        if any(event["payload"]["control_epoch"] >= epoch for event in prior):
            raise CausalJournalError("recovery replay requires a strictly newer control epoch")
        return self._append_locked(run_id, "RESOURCE_RECOVERY_CLAIMED", {
            "intent_run_seq": intent_run_seq,
            "step_id": payload["step_id"], "attempt_identity": payload["attempt_identity"],
            "control_epoch": epoch,
            "stage": stage, "allocation_run_seq": allocation_seq,
            "allocation_identity": allocation_identity,
            "cleanup_only": True, "native_absence_proven": False,
            "mounts_proven": False, "process_tree_proven": False,
            "teardown_proven": False,
        })

    def transfer_control(self, run_id: str, *, epoch: int, lease_identity: str,
                         next_epoch: int, next_lease_identity: str) -> None:
        if type(next_epoch) is not int or next_epoch <= epoch or not next_lease_identity:
            raise CausalJournalError("control epoch must strictly increase")
        def commit() -> None:
            self._check_fence(self._row(run_id), epoch=epoch, lease_identity=lease_identity)
            self.connection.execute("UPDATE lace_run_authority SET control_epoch=?,lease_identity=? WHERE run_id=?",
                                    (next_epoch, next_lease_identity, run_id))
        self._transaction(commit)

    def transfer_control_in(self, connection: sqlite3.Connection, run_id: str, *,
                            epoch: int, lease_identity: str,
                            next_epoch: int, next_lease_identity: str) -> None:
        """DB1B2 pre-live transfer participant in the original DB owner's cut.

        This is not a new Fleet lease-transfer authority. The caller also
        proves the durable signer fence before using this helper.
        """
        if connection is not self.connection or self._database_owner is None:
            raise CausalJournalError("original LocalDatabase transfer owner required")
        self._database_owner.require_atomic_write_cut(connection)
        if type(next_epoch) is not int or next_epoch <= epoch or not next_lease_identity:
            raise CausalJournalError("control epoch must strictly increase")
        self._check_fence(self._row(run_id), epoch=epoch, lease_identity=lease_identity)
        connection.execute(
            "UPDATE lace_run_authority SET control_epoch=?, lease_identity=? WHERE run_id=?",
            (next_epoch, next_lease_identity, run_id),
        )

    def seal(self, run_id: str, *, epoch: int, lease_identity: str,
             outcome: str, auth: ReceiptAuthenticator, receipt: AuthenticatedReceipt,
             before_commit: Callable[[], None] | None = None) -> None:
        # No terminal cut without Run-scoped quiescence and a signed exact journal cut.
        signed = auth.verify(receipt, domain="terminal")
        def commit() -> None:
            row = self._row(run_id)
            self._check_fence(row, epoch=epoch, lease_identity=lease_identity)
            last = self.connection.execute(
                "SELECT event_kind,event_payload FROM lace_run_events WHERE run_id=? AND run_seq=?",
                (run_id, row[3]),
            ).fetchone()
            if not last or last[0] != "QUIESCENCE_PROVEN" or json.loads(last[1]) != {"scope": "run"}:
                raise CausalJournalError("Run-scoped quiescence has not been durably established")
            poison = self.connection.execute("""SELECT event_kind FROM lace_run_events
                WHERE run_id=? AND event_kind IN ('CONTROL_LEASE_LOST','JOURNAL_APPEND_FAILED')
                LIMIT 1""", (run_id,)).fetchone()
            if poison:
                raise CausalJournalError("control/journal failure prevents terminal seal")
            if outcome not in {member.value for member in CanonicalOutcome} - {"ADMISSION_REJECTED"} or signed != {
                "protocol": PROTOCOL_VERSION, "run_id": run_id,
                "contract_identity": row[0], "final_run_seq": row[3] + 1,
                "preterminal_head_identity": row[4], "outcome": outcome,
            }:
                raise CausalJournalError("terminal receipt does not bind exact journal cut")
            self._append_locked(run_id, "RUN_OUTCOME", {"outcome": outcome})
            self.connection.execute("""UPDATE lace_run_authority SET state='SEALED',
                closure_receipt=?, closure_mac=?,closure_key_id=?,closure_domain=? WHERE run_id=?""",
                (receipt.payload, receipt.mac_hex, receipt.key_id, receipt.domain, run_id))
            if before_commit:
                before_commit()
        self._transaction(commit)

    def mark_unresolved(self, run_id: str, *, auth: ReceiptAuthenticator,
                        receipt: AuthenticatedReceipt) -> None:
        """Control-plane authenticated unresolved truth; cannot rewrite or seal later."""
        signed = auth.verify(receipt, domain="unresolved")
        def commit() -> None:
            row = self._row(run_id)
            if row[5] != "ACTIVE" or signed != {
                "protocol": PROTOCOL_VERSION, "run_id": run_id,
                "contract_identity": row[0], "control_epoch": row[1],
                "last_run_seq": row[3], "head_identity": row[4],
                "state": "JOURNAL_UNRESOLVED",
            }:
                raise CausalJournalError("unresolved receipt does not bind exact control state")
            self.connection.execute("""UPDATE lace_run_authority SET state='UNRESOLVED',
                closure_receipt=?,closure_mac=?,closure_key_id=?,closure_domain=? WHERE run_id=?""",
                (receipt.payload, receipt.mac_hex, receipt.key_id, receipt.domain, run_id))
        self._transaction(commit)

    def verify(self, run_id: str, *, auth: ReceiptAuthenticator) -> tuple[dict[str, Any], ...]:
        """Independently verify the admission and, if closed, its terminal/lifecycle receipt."""
        row = self._row(run_id)
        if auth is None:
            raise CausalJournalError("trusted authenticator required for authoritative read")
        signed_admission = self.connection.execute("""SELECT admitted_contract, admission_receipt,
            admission_mac, admission_key_id, admission_domain FROM lace_run_authority
            WHERE run_id=?""", (run_id,)).fetchone()
        if signed_admission is None:
            raise CausalJournalError("missing admitted source authority")
        candidate = AdmittedRunContract(signed_admission[0], row[0])
        candidate.verify()
        bound = auth.verify(AuthenticatedReceipt(signed_admission[4], signed_admission[3],
                                                 signed_admission[1], signed_admission[2]), domain="admission")
        if bound != {"decision": "ADMITTED", "run_id": run_id,
                     "contract_identity": row[0], "protocol": PROTOCOL_VERSION}:
            raise CausalJournalError("admission receipt/contract mismatch")
        events = self.connection.execute("""SELECT run_seq,event_kind,event_payload,previous_identity,event_identity
            FROM lace_run_events WHERE run_id=? ORDER BY run_seq""", (run_id,)).fetchall()
        previous = GENESIS
        last_transfer = None
        seen_signer_epoch = False
        seen_agent_source_steps: set[str] = set()
        seen_agent_stages: set[int] = set()
        for seq, (n, kind, payload, prev, identity) in enumerate(events, start=1):
            if n != seq or prev != previous or identity != _event_identity(run_id, n, kind, payload, prev):
                raise CausalJournalError("journal gap, reorder, replay or content tampering")
            if n == 1 and (kind != "RUN_ADMITTED" or
                          json.loads(payload) != {"contract_identity": row[0]}):
                raise CausalJournalError("missing/mismatched authoritative admission event")
            if kind == "RESOURCE_PHYSICAL_SAMPLE_RECORDED":
                # One ORIGINAL Fleet journal event, never an independent
                # physical-fact signature or terminal containment proof.
                # Even a valid hash chain must not smuggle a PASS claim in its
                # payload or silently rewrite the observed kernel snapshot.
                from oh_goon.core.ports.physical_observation import (
                    PhysicalKernelFact, PhysicalKernelSnapshot,
                )
                item = json.loads(payload)
                required = {
                    "schema", "run_id", "intent_run_seq", "claim_run_seq",
                    "control_epoch", "subject_identity", "envelope_identity",
                    "mount_intent_identity", "scope", "scope_identity", "snapshot_identity",
                    "sample_identity", "observed", "classification",
                    "observer_custody", "coverage", "effect_permission",
                    "physical_absence_proven", "quiescence_proven", "teardown_proven",
                }
                extended = {
                    "sample_round", "previous_sample_run_seq",
                    "cleanup_started_run_seq", "cleanup_recorded_run_seq",
                }
                v2 = type(item) is dict and item.get("schema") == "oh-goon/original-unqualified-physical-sample/v2"
                if type(item) is not dict or set(item) != (required | extended if v2 else required):
                    raise CausalJournalError("malformed original physical sample event")
                def is_digest(value: object) -> bool:
                    return (type(value) is str and len(value) == 71
                            and value.startswith("sha256:")
                            and all(ch in "0123456789abcdef" for ch in value[7:]))
                earlier = [(seq_id, event_kind, json.loads(event_payload))
                           for seq_id, event_kind, event_payload, _, _ in events[:seq - 1]]
                claims = [event for event in earlier if event[0] == item["claim_run_seq"]
                          and event[1] == "RESOURCE_RECOVERY_CLAIMED"]
                inventories = [event for event in earlier if event[1] == "RESOURCE_ORPHAN_INVENTORIED"
                               and event[2].get("claim_run_seq") == item["claim_run_seq"]]
                if (item["schema"] not in (
                        "oh-goon/original-unqualified-physical-sample/v1",
                        "oh-goon/original-unqualified-physical-sample/v2")
                        or item["run_id"] != run_id
                        or type(item["intent_run_seq"]) is not int or item["intent_run_seq"] < 2
                        or type(item["claim_run_seq"]) is not int
                        or type(item["control_epoch"]) is not int or item["control_epoch"] < 1
                        or any(not is_digest(item[field]) for field in (
                            "subject_identity", "envelope_identity", "mount_intent_identity",
                            "scope_identity", "snapshot_identity", "sample_identity"))
                        or item["classification"] != "ORIGINAL_OWNER_READ_ONLY_UNQUALIFIED"
                        or item["observer_custody"] != "UNVERIFIED"
                        or item["coverage"] != "INCOMPLETE_NATIVE_RESOURCE_SCOPE"
                        or item["effect_permission"] != "NONE"
                        or any(item[flag] is not False for flag in (
                            "physical_absence_proven", "quiescence_proven", "teardown_proven"))
                        or len(claims) != 1 or len(inventories) != 1
                        or inventories[0][2].get("intent_run_seq") != item["intent_run_seq"]
                        or inventories[0][2].get("control_epoch") != item["control_epoch"]
                        or inventories[0][2].get("subject_identity") != item["subject_identity"]
                        or (not v2 and any(previous_kind == "RESOURCE_PHYSICAL_SAMPLE_RECORDED"
                               and previous_payload.get("sample_identity") == item["sample_identity"]
                               for _, previous_kind, previous_payload in earlier))):
                    raise CausalJournalError("original physical sample has invalid causal authority")
                if v2:
                    from oh_goon.runtime.enforcer_lace_original_physical_sampling import original_sample_position
                    previous_events = [{"run_seq": number, "kind": event_kind, "payload": data}
                                       for number, event_kind, data in earlier]
                    expected_position = original_sample_position(
                        previous_events, claim_run_seq=item["claim_run_seq"],
                        intent_run_seq=item["intent_run_seq"],
                        subject_identity=item["subject_identity"],
                    )
                    supplied_position = (
                        item["sample_round"], item["previous_sample_run_seq"],
                        item["cleanup_started_run_seq"], item["cleanup_recorded_run_seq"],
                    )
                    if (supplied_position != expected_position
                            or type(item["sample_round"]) is not int
                            or any(value is not None and type(value) is not int
                                   for value in supplied_position[1:])):
                        raise CausalJournalError("original physical observation round or cleanup ancestry forged")
                try:
                    observed = item["observed"]
                    if type(observed) is not list or not observed:
                        raise ValueError("missing bounded kernel components")
                    facts = PhysicalKernelSnapshot(tuple(
                        (observation["component"], PhysicalKernelFact(
                            observation["state"], observation["detail"]))
                        for observation in observed
                        if type(observation) is dict
                        and set(observation) == {"component", "state", "detail"}
                    ))
                    if len(facts.components) != len(observed) or facts.identity != item["snapshot_identity"]:
                        raise ValueError("kernel observation identity mismatch")
                    from oh_goon.core.ports.physical_observation import PhysicalObservationScope
                    from dataclasses import asdict
                    scope = item["scope"]
                    if type(scope) is not dict or set(scope) != set(PhysicalObservationScope.__dataclass_fields__):
                        raise ValueError("sample scope incomplete")
                    targets = scope["mount_targets"]
                    if (type(targets) is not list or not targets or len(targets) > 64
                            or any(type(target) is not str or not target.startswith("/")
                                   for target in targets)):
                        raise ValueError("sample mount targets invalid")
                    reconstructed = PhysicalObservationScope(**{**scope, "mount_targets": tuple(targets)})
                    if (reconstructed.run_id != run_id
                            or reconstructed.subject_identity != item["subject_identity"]
                            or digest("oh-goon/b27/original-observation-scope/v1",
                                      canonical_bytes(scope)) != item["scope_identity"]
                            or digest("oh-goon/b27/original-physical-candidate/v1", canonical_bytes({
                                "envelope_identity": item["envelope_identity"],
                                "scope": {**asdict(reconstructed), "mount_targets": targets},
                                "kernel_snapshot_identity": facts.identity,
                                "mount_intent_identity": item["mount_intent_identity"],
                                "classification": item["classification"],
                                "effect_permission": item["effect_permission"],
                            })) != item["sample_identity"]):
                        raise ValueError("Run-bound observation identity mismatch")
                except (ValueError, TypeError, KeyError) as exc:
                    raise CausalJournalError("original physical sample kernel transcript invalid") from exc
            if kind == "RUN_SIGNER_EPOCH_BOUND":
                # Strict ingress signs the original trust epoch on the same
                # causal chain and SQLite admission commit. A chain hash alone
                # does not attest signer generation; require the original MAC.
                item = json.loads(payload)
                if (n != 2 or seen_signer_epoch or type(item) is not dict or
                        set(item) != {"binding", "signature", "signature_domain", "signature_key_id"}):
                    raise CausalJournalError("missing or duplicated original signed issuer epoch")
                binding = item["binding"]
                if (type(binding) is not dict or
                        set(binding) != {"protocol", "run_id", "contract_identity",
                                         "issuer_key_id", "trust_epoch", "policy_identity", "root_identity"} or
                        binding["protocol"] != PROTOCOL_VERSION or
                        binding["run_id"] != run_id or binding["contract_identity"] != row[0] or
                        binding["issuer_key_id"] != auth.key_id or
                        type(binding["trust_epoch"]) is not int or binding["trust_epoch"] < 1 or
                        not all(type(binding[k]) is str and
                                binding[k].startswith("sha256:") and len(binding[k]) == 71
                                for k in ("policy_identity", "root_identity")) or
                        item["signature_domain"] != "admission-signer-epoch" or
                        item["signature_key_id"] != auth.key_id):
                    raise CausalJournalError("original signed issuer epoch binding invalid")
                epoch_receipt = AuthenticatedReceipt(
                    item["signature_domain"], item["signature_key_id"],
                    canonical_bytes(binding), item["signature"],
                )
                if auth.verify(epoch_receipt, domain="admission-signer-epoch") != binding:
                    raise CausalJournalError("original signed issuer epoch authentication failed")
                seen_signer_epoch = True
            if kind == "AGENT_SOURCE_PREWORK_RESERVED":
                item = json.loads(payload)
                if (type(item) is not dict or set(item) != {
                        "binding", "signature", "signature_domain", "signature_key_id"}):
                    raise CausalJournalError("malformed original Agent Step source reservation")
                binding = item["binding"]
                expected_keys = {
                    "protocol", "run_id", "contract_identity", "step_id", "attempt_identity",
                    "control_epoch", "lease_identity", "executor_generation", "signer_trust_epoch",
                    "handoff_identity", "packet_identity", "packet_bytes", "authority", "effect_permission",
                }
                valid_keys = (
                    expected_keys, expected_keys | {"requested_effects_identity"},
                    expected_keys | {"requested_effects_identity", "effect_limits_identity"},
                    expected_keys | {"effect_limits_identity"},
                )
                if (type(binding) is not dict or set(binding) not in valid_keys
                        or binding["protocol"] != PROTOCOL_VERSION
                        or binding["run_id"] != run_id or binding["contract_identity"] != row[0]
                        or type(binding["step_id"]) is not str or not binding["step_id"]
                        or type(binding["attempt_identity"]) is not str
                        or not binding["attempt_identity"].startswith("sha256:")
                        or len(binding["attempt_identity"]) != 71
                        or type(binding["packet_identity"]) is not str
                        or not binding["packet_identity"].startswith("sha256:")
                        or len(binding["packet_identity"]) != 71
                        or type(binding["packet_bytes"]) is not int or binding["packet_bytes"] < 1
                        or type(binding["control_epoch"]) is not int or binding["control_epoch"] < 1
                        or type(binding["executor_generation"]) is not int or binding["executor_generation"] < 1
                        or type(binding["signer_trust_epoch"]) is not int or binding["signer_trust_epoch"] < 1
                        or type(binding["lease_identity"]) is not str or not binding["lease_identity"]
                        or type(binding["handoff_identity"]) is not str
                        or not binding["handoff_identity"].startswith("agent-step-prework-handoff-v1-")
                        or binding["authority"] != "PREWORK_NOT_ADMITTED"
                        or binding["effect_permission"] != "NONE"
                        or item["signature_domain"] != "agent-source-prework"
                        or item["signature_key_id"] != auth.key_id):
                    raise CausalJournalError("invalid Agent Step source reservation binding")
                signed_step = candidate.fields["step_envelopes"].get(binding["step_id"])
                signed_demand = (signed_step.get("agent_requested_effects")
                                 if type(signed_step) is dict else None)
                if (type(signed_step) is not dict
                        or signed_step.get("workload_kind") != "agent"
                        or signed_step.get("agent_handoff_identity") != binding["handoff_identity"]
                        or (("requested_effects_identity" in binding)
                            != ("agent_requested_effects" in signed_step))):
                    raise CausalJournalError("Agent Step prework differs from signed Run/Step envelope")
                if "requested_effects_identity" in binding:
                    if (type(signed_demand) is not dict or
                            binding["requested_effects_identity"] != digest(
                                "oh-goon/agent-requested-effects/v1",
                                canonical_bytes(signed_demand),
                            )):
                        raise CausalJournalError("Agent Step effect demand identity changed")
                # Signed ceiling is checked again on readback, not merely when
                # the issuing method ran. A re-signed journal event cannot
                # widen the separately admitted ceiling or forge its identity.
                signed_limit_identity = _independently_scoped_agent_effects(
                    signed_demand if "requested_effects_identity" in binding else None,
                    signed_step,
                )
                if (("effect_limits_identity" in binding) != (signed_limit_identity is not None)
                        or (signed_limit_identity is not None and
                            binding["effect_limits_identity"] != signed_limit_identity)):
                    raise CausalJournalError("Agent Enforcer effect ceiling identity changed")
                # Authenticator is the ORIGINAL admitted issuer, not another signer.
                receipt = AuthenticatedReceipt(item["signature_domain"], item["signature_key_id"],
                                               canonical_bytes(binding), item["signature"])
                if auth.verify(receipt, domain="agent-source-prework") != binding:
                    raise CausalJournalError("Agent Step source reservation authentication failed")
                if not seen_signer_epoch or binding["step_id"] in seen_agent_source_steps:
                    raise CausalJournalError("unsigned or duplicate original Agent Step source reservation")
                seen_agent_source_steps.add(binding["step_id"])
            if kind == "AGENT_SOURCE_PREWORK_STAGED":
                item = json.loads(payload)
                if type(item) is not dict or set(item) != {
                        "binding", "signature", "signature_domain", "signature_key_id"}:
                    raise CausalJournalError("invalid signed Agent prework stage")
                binding = item["binding"]
                required_binding = {
                    "run_id", "step_id", "attempt_identity", "reservation_seq",
                    "contract_identity", "stage", "effect_permission", "physical_sandbox_proven"}
                if (type(binding) is not dict or set(binding) not in (
                        required_binding, required_binding | {"source_mount"})):
                    raise CausalJournalError("malformed Agent stage binding")
                if "source_mount" in binding:
                    from oh_goon.core.ports.original_mount_intent import OriginalSandboxMount
                    mount = binding["source_mount"]
                    if type(mount) is not dict or set(mount) != {"source", "target", "sha256"}:
                        raise CausalJournalError("signed Agent source mount is malformed")
                    try:
                        checked_mount = OriginalSandboxMount(**mount)
                    except (TypeError, ValueError) as exc:
                        raise CausalJournalError("signed Agent source mount is invalid") from exc
                    if checked_mount.target != "/opt/oh-goon/agent-source":
                        raise CausalJournalError("signed Agent source mount target changed")
                linked = [e for e in events[:seq-1] if e[1] == "AGENT_SOURCE_PREWORK_RESERVED"
                          and e[0] == binding["reservation_seq"]]
                if len(linked) != 1 or binding["reservation_seq"] in seen_agent_stages:
                    raise CausalJournalError("Agent stage has missing or duplicate reservation")
                original = json.loads(linked[0][2])["binding"]
                stage = binding["stage"]
                if (binding["run_id"] != run_id or binding["contract_identity"] != row[0]
                        or binding["step_id"] != original["step_id"]
                        or binding["attempt_identity"] != original["attempt_identity"]
                        or binding["effect_permission"] != "NONE"
                        or binding["physical_sandbox_proven"] is not False
                        or type(stage) is not dict or set(stage) != {
                            "schema", "handoff_identity", "stage_identity", "file_count",
                            "readback_verified", "effect_permission", "physical_sandbox_proven"}
                        or stage["schema"] != "oh-goon.agent-prework-attempt-stage/v1"
                        or stage["handoff_identity"] != original["handoff_identity"]
                        or stage["readback_verified"] is not True
                        or stage["effect_permission"] != "NONE"
                        or stage["physical_sandbox_proven"] is not False
                        or type(stage["file_count"]) is not int or stage["file_count"] < 2
                        or type(stage["stage_identity"]) is not str
                        or not stage["stage_identity"].startswith("sha256:")
                        or len(stage["stage_identity"]) != 71
                        or item["signature_domain"] != "agent-source-prework-stage"
                        or item["signature_key_id"] != auth.key_id):
                    raise CausalJournalError("invalid original Agent stage evidence")
                signed = AuthenticatedReceipt(item["signature_domain"], item["signature_key_id"],
                                               canonical_bytes(binding), item["signature"])
                if auth.verify(signed, domain="agent-source-prework-stage") != binding:
                    raise CausalJournalError("Agent source staging signature invalid")
                seen_agent_stages.add(binding["reservation_seq"])
            if kind == "AGENT_TOOL_LAUNCH_GRANTED":
                item = json.loads(payload)
                if (type(item) is not dict or set(item) != {
                        "binding", "signature", "signature_domain", "signature_key_id"}
                        or item["signature_domain"] != "agent-tool-launch-grant"
                        or item["signature_key_id"] != auth.key_id):
                    raise CausalJournalError("invalid original Agent tool grant receipt")
                binding = item["binding"]
                keys = {"protocol", "run_id", "contract_identity", "step_id", "attempt_identity",
                        "attempt_number", "command_ordinal", "control_epoch", "lease_identity",
                        "executor_generation", "signer_trust_epoch", "source_reservation_seq",
                        "source_stage_seq", "requested_effects_identity", "effect_limits_identity",
                        "tool_name", "command_identity", "authority", "effect_permission"}
                if (type(binding) is not dict or set(binding) != keys
                        or binding["protocol"] != PROTOCOL_VERSION
                        or binding["run_id"] != run_id or binding["contract_identity"] != row[0]
                        or type(binding["step_id"]) is not str
                        or type(binding["attempt_identity"]) is not str
                        or type(binding["attempt_number"]) is not int or binding["attempt_number"] < 1
                        or type(binding["command_ordinal"]) is not int or binding["command_ordinal"] < 1
                        or type(binding["control_epoch"]) is not int or binding["control_epoch"] < 1
                        or type(binding["lease_identity"]) is not str or not binding["lease_identity"]
                        or type(binding["executor_generation"]) is not int or binding["executor_generation"] < 1
                        or type(binding["signer_trust_epoch"]) is not int or binding["signer_trust_epoch"] < 1
                        or type(binding["tool_name"]) is not str or not binding["tool_name"]
                        or binding["authority"] != "ENFORCER_GRANT_REQUIRES_LACE_RESOURCE_INTENT"
                        or binding["effect_permission"] != "NONE_UNTIL_LACE"):
                    raise CausalJournalError("malformed original Agent tool grant")
                for name in ("attempt_identity", "command_identity", "requested_effects_identity",
                             "effect_limits_identity"):
                    _require_digest(binding[name], name=name)
                sources = [e for e in events[:seq - 1]
                           if e[1] == "AGENT_SOURCE_PREWORK_RESERVED"
                           and e[0] == binding["source_reservation_seq"]]
                stages = [e for e in events[:seq - 1]
                          if e[1] == "AGENT_SOURCE_PREWORK_STAGED"
                          and e[0] == binding["source_stage_seq"]]
                if len(sources) != 1 or len(stages) != 1:
                    raise CausalJournalError("Agent tool grant lacks original source and stage")
                source = json.loads(sources[0][2])["binding"]
                stage = json.loads(stages[0][2])["binding"]
                signed_step = candidate.fields["step_envelopes"].get(binding["step_id"])
                if (type(signed_step) is not dict
                        or signed_step.get("workload_kind") != "agent"
                        or source["step_id"] != binding["step_id"]
                        or source["attempt_identity"] != binding["attempt_identity"]
                        or stage["attempt_identity"] != binding["attempt_identity"]
                        or stage["reservation_seq"] != binding["source_reservation_seq"]
                        or source["control_epoch"] != binding["control_epoch"]
                        or source["lease_identity"] != binding["lease_identity"]
                        or source["executor_generation"] != binding["executor_generation"]
                        or source["signer_trust_epoch"] != binding["signer_trust_epoch"]
                        or source.get("requested_effects_identity") != binding["requested_effects_identity"]
                        or source.get("effect_limits_identity") != binding["effect_limits_identity"]
                        or binding["effect_limits_identity"] != _independently_scoped_agent_effects(
                            signed_step.get("agent_requested_effects"), signed_step)
                        or binding["tool_name"] not in signed_step["agent_requested_effects"]["capabilities"]["tools"]):
                    raise CausalJournalError("original Agent tool grant contradicts admitted demand")
                prior_grants = [json.loads(e[2])["binding"] for e in events[:seq - 1]
                                if e[1] == "AGENT_TOOL_LAUNCH_GRANTED"]
                if any(prior["step_id"] == binding["step_id"]
                       and prior["attempt_identity"] == binding["attempt_identity"]
                       and prior["command_ordinal"] == binding["command_ordinal"]
                       for prior in prior_grants):
                    raise CausalJournalError("duplicate original Agent tool grant")
                receipt = AuthenticatedReceipt(
                    item["signature_domain"], item["signature_key_id"],
                    canonical_bytes(binding), item["signature"],
                )
                if auth.verify(receipt, domain="agent-tool-launch-grant") != binding:
                    raise CausalJournalError("original Agent tool grant signature changed")
            if kind == "RUN_RECOVERY_CUSTODY_TRANSFERRED":
                # Transfer claims require the original issuer's distinct MAC domain.
                # A recomputed hash chain alone cannot authorize custody movement.
                item = json.loads(payload)
                if (type(item) is not dict or set(item) != {
                        "binding", "signature", "signature_domain", "signature_key_id"}):
                    raise CausalJournalError("malformed signed Fleet custody transfer")
                binding = item["binding"]
                if (type(binding) is not dict or
                        item["signature_domain"] != "recovery-transfer" or
                        item["signature_key_id"] != auth.key_id):
                    raise CausalJournalError("foreign signed Fleet custody transfer")
                transfer_receipt = AuthenticatedReceipt(
                    item["signature_domain"], item["signature_key_id"],
                    canonical_bytes(binding), item["signature"],
                )
                if auth.verify(transfer_receipt, domain="recovery-transfer") != binding:
                    raise CausalJournalError("Fleet custody signature changed")
                if (binding.get("protocol") != PROTOCOL_VERSION
                        or binding.get("run_id") != run_id
                        or binding.get("contract_identity") != row[0]
                        or binding.get("issuer_key_id") != auth.key_id
                        or binding.get("decision") != "RECOVERY_CUSTODY_ONLY"
                        or binding.get("effect_permission") != "NONE"
                        or binding.get("step_replay_permitted") is not False
                        or binding.get("native_absence_proven") is not False
                        or binding.get("predecessor_reconciliation") != "UNRESOLVED"
                        or type(binding.get("predecessor_epoch")) is not int
                        or type(binding.get("next_epoch")) is not int
                        or binding["next_epoch"] != binding["predecessor_epoch"] + 1):
                    raise CausalJournalError("signed Fleet custody transfer violates fail-closed policy")
                last_transfer = binding
            previous = identity
        if len(events) != row[3] or previous != row[4]:
            raise CausalJournalError("journal tail truncation / inconsistent durable head")
        if last_transfer is not None and (
                row[1] != last_transfer["next_epoch"]
                or row[2] != last_transfer["successor_token"]):
            raise CausalJournalError("original Fleet custody and signed transfer disagree")
        if row[5] in {"SEALED", "UNRESOLVED"}:
            r = self.connection.execute("""SELECT closure_receipt,closure_mac,closure_key_id,closure_domain
                FROM lace_run_authority WHERE run_id=?""", (run_id,)).fetchone()
            if not r or any(v is None for v in r):
                raise CausalJournalError("missing terminal receipt")
            if row[5] == "UNRESOLVED":
                expected = {"protocol": PROTOCOL_VERSION, "run_id": run_id,
                    "contract_identity": row[0], "control_epoch": row[1],
                    "last_run_seq": row[3], "head_identity": row[4],
                    "state": "JOURNAL_UNRESOLVED"}
                domain = "unresolved"
            else:
                if not events or events[-1][1] != "RUN_OUTCOME":
                    raise CausalJournalError("missing terminal Run outcome")
                expected = {"protocol": PROTOCOL_VERSION, "run_id": run_id,
                    "contract_identity": row[0], "final_run_seq": row[3],
                    "preterminal_head_identity": events[-1][3],
                    "outcome": json.loads(events[-1][2])["outcome"]}
                domain = "terminal"
            signed = auth.verify(AuthenticatedReceipt(r[3], r[2], r[0], r[1]), domain=domain)
            if signed != expected:
                raise CausalJournalError("authenticated closure and causal history disagree")
        return tuple({"run_seq": n, "kind": k, "payload": json.loads(p)} for n, k, p, _, _ in events)
