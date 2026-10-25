from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, TypeAdapter

from oh_goon.core.errors import JournalCorruption
from oh_goon.core.models.agent_repair import AgentCampaignFact
from oh_goon.core.models.operation import OperationReport
from oh_goon.core.models.reconciliation import (
    RepositoryEffectAcknowledgementEvidence,
    RepositoryEffectReconciliationEvidence,
)
from oh_goon.core.models.run_events import FleetRunEvent
from oh_goon.runtime.journal_facts import (
    AgentCampaignCommitted,
    EffectAcknowledgementCommitted,
    EffectReconciliationCommitted,
    FleetOperationCommitted,
    JournalEntry,
    OperationCommitted,
    RunEventCommitted,
)

GENESIS_HASH = "0" * 64
_FLEET_RUN_EVENT_ADAPTER: TypeAdapter[FleetRunEvent] = TypeAdapter(FleetRunEvent)
_EFFECT_RECONCILIATION_ADAPTER = TypeAdapter(RepositoryEffectReconciliationEvidence)
_CAMPAIGN_ADAPTER: TypeAdapter[AgentCampaignFact] = TypeAdapter(AgentCampaignFact)
_EFFECT_ACKNOWLEDGEMENT_ADAPTER = TypeAdapter(RepositoryEffectAcknowledgementEvidence)


class JournalFactKind(StrEnum):
    CAMPAIGN = "CAMPAIGN"
    OPERATION = "OPERATION"
    RUN_EVENT = "RUN_EVENT"
    FLEET_OPERATION = "FLEET_OPERATION"
    EFFECT_RECONCILIATION = "EFFECT_RECONCILIATION"
    EFFECT_ACKNOWLEDGEMENT = "EFFECT_ACKNOWLEDGEMENT"


@dataclass(frozen=True, slots=True)
class EncodedJournalFact:
    kind: JournalFactKind
    report_json: str | None
    event_json: str | None
    effect_json: str | None


class JournalFactCodec:
    """Canonical serialization and integrity verification for durable journal facts."""

    @staticmethod
    def encode_operation(report: OperationReport) -> EncodedJournalFact:
        return EncodedJournalFact(
            kind=JournalFactKind.OPERATION,
            report_json=_canonical_model_json(report),
            event_json=None,
            effect_json=None,
        )

    @staticmethod
    def encode_run_event(event: FleetRunEvent) -> EncodedJournalFact:
        return EncodedJournalFact(
            kind=JournalFactKind.RUN_EVENT,
            report_json=None,
            event_json=_canonical_model_json(event),
            effect_json=None,
        )

    @staticmethod
    def encode_fleet_operation(report: OperationReport, event: FleetRunEvent) -> EncodedJournalFact:
        return EncodedJournalFact(
            kind=JournalFactKind.FLEET_OPERATION,
            report_json=_canonical_model_json(report),
            event_json=_canonical_model_json(event),
            effect_json=None,
        )

    @staticmethod
    def encode_effect_reconciliation(
        evidence: RepositoryEffectReconciliationEvidence,
    ) -> EncodedJournalFact:
        return EncodedJournalFact(
            kind=JournalFactKind.EFFECT_RECONCILIATION,
            report_json=None,
            event_json=None,
            effect_json=_canonical_model_json(evidence),
        )

    @staticmethod
    def encode_effect_acknowledgement(
        evidence: RepositoryEffectAcknowledgementEvidence,
    ) -> EncodedJournalFact:
        return EncodedJournalFact(
            kind=JournalFactKind.EFFECT_ACKNOWLEDGEMENT,
            report_json=None,
            event_json=None,
            effect_json=_canonical_model_json(evidence),
        )

    @staticmethod
    def encode_campaign(fact: AgentCampaignFact) -> EncodedJournalFact:
        return EncodedJournalFact(
            kind=JournalFactKind.CAMPAIGN,
            report_json=None,
            event_json=None,
            effect_json=_canonical_model_json(fact),
        )

    @staticmethod
    def hash_fact(
        *,
        previous_hash: str,
        session_id: str,
        encoded: EncodedJournalFact,
        committed_at: str,
    ) -> str:
        payload = json.dumps(
            {
                "previous_hash": previous_hash,
                "session_id": session_id,
                "kind": encoded.kind.value,
                "report_json": encoded.report_json,
                "event_json": encoded.event_json,
                "effect_json": encoded.effect_json,
                "committed_at": committed_at,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def decode(
        *,
        revision: int,
        session_id: str,
        kind: str,
        report_json: str | None,
        event_json: str | None,
        effect_json: str | None,
        committed_at: str,
        previous_hash: str,
        fact_hash: str,
        expected_previous_hash: str,
    ) -> JournalEntry:
        if previous_hash != expected_previous_hash:
            raise JournalCorruption(f"Journal hash chain is broken before revision {revision}")
        try:
            fact_kind = JournalFactKind(kind)
        except ValueError as exc:
            raise JournalCorruption(
                f"Unknown journal fact kind at revision {revision}: {kind}"
            ) from exc
        encoded = EncodedJournalFact(fact_kind, report_json, event_json, effect_json)
        expected_hash = JournalFactCodec.hash_fact(
            previous_hash=previous_hash,
            session_id=session_id,
            encoded=encoded,
            committed_at=committed_at,
        )
        if fact_hash != expected_hash:
            raise JournalCorruption(f"Journal fact hash mismatch at revision {revision}")
        try:
            if fact_kind is JournalFactKind.CAMPAIGN:
                if report_json is not None or event_json is not None or effect_json is None:
                    raise ValueError("Invalid campaign Journal fact shape")
                return AgentCampaignCommitted(
                    revision, session_id, _CAMPAIGN_ADAPTER.validate_json(effect_json)
                )
            report = (
                OperationReport.model_validate(json.loads(report_json))
                if report_json is not None
                else None
            )
            event = (
                _FLEET_RUN_EVENT_ADAPTER.validate_python(json.loads(event_json))
                if event_json is not None
                else None
            )
            effect: (
                RepositoryEffectReconciliationEvidence
                | RepositoryEffectAcknowledgementEvidence
                | None
            ) = None
            if effect_json is not None:
                payload = json.loads(effect_json)
                if fact_kind is JournalFactKind.EFFECT_RECONCILIATION:
                    effect = _EFFECT_RECONCILIATION_ADAPTER.validate_python(payload)
                elif fact_kind is JournalFactKind.EFFECT_ACKNOWLEDGEMENT:
                    effect = _EFFECT_ACKNOWLEDGEMENT_ADAPTER.validate_python(payload)
        except (json.JSONDecodeError, ValueError) as exc:
            raise JournalCorruption(f"Journal payload is invalid at revision {revision}") from exc
        if (
            fact_kind is JournalFactKind.OPERATION
            and report is not None
            and event is None
            and effect is None
        ):
            return OperationCommitted(revision, session_id, report)
        if (
            fact_kind is JournalFactKind.RUN_EVENT
            and report is None
            and event is not None
            and effect is None
        ):
            return RunEventCommitted(revision, session_id, event)
        if (
            fact_kind is JournalFactKind.FLEET_OPERATION
            and report is not None
            and event is not None
            and effect is None
        ):
            return FleetOperationCommitted(revision, session_id, report, event)
        if (
            fact_kind is JournalFactKind.EFFECT_RECONCILIATION
            and report is None
            and event is None
            and isinstance(effect, RepositoryEffectReconciliationEvidence)
        ):
            return EffectReconciliationCommitted(revision, session_id, effect)
        if (
            fact_kind is JournalFactKind.EFFECT_ACKNOWLEDGEMENT
            and report is None
            and event is None
            and isinstance(effect, RepositoryEffectAcknowledgementEvidence)
        ):
            return EffectAcknowledgementCommitted(revision, session_id, effect)
        raise JournalCorruption(f"Journal fact shape does not match kind at revision {revision}")


def _canonical_model_json(model: BaseModel) -> str:
    return json.dumps(
        model.model_dump(mode="json", exclude_computed_fields=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
