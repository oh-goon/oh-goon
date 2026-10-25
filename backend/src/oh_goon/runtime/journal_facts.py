from __future__ import annotations

from dataclasses import dataclass

from oh_goon.core.models.agent_repair import AgentCampaignFact
from oh_goon.core.models.operation import OperationReport
from oh_goon.core.models.reconciliation import (
    RepositoryEffectAcknowledgementEvidence,
    RepositoryEffectReconciliationEvidence,
)
from oh_goon.core.models.run_events import FleetRunEvent


@dataclass(frozen=True, slots=True)
class OperationCommitted:
    revision: int
    session_id: str
    report: OperationReport


@dataclass(frozen=True, slots=True)
class RunEventCommitted:
    revision: int
    session_id: str
    event: FleetRunEvent


@dataclass(frozen=True, slots=True)
class EffectReconciliationCommitted:
    revision: int
    session_id: str
    evidence: RepositoryEffectReconciliationEvidence


@dataclass(frozen=True, slots=True)
class EffectAcknowledgementCommitted:
    revision: int
    session_id: str
    evidence: RepositoryEffectAcknowledgementEvidence


@dataclass(frozen=True, slots=True)
class FleetOperationCommitted:
    """One atomic durable fact: operation report + accepting completion event."""

    revision: int
    session_id: str
    report: OperationReport
    event: FleetRunEvent


@dataclass(frozen=True, slots=True)
class AgentCampaignCommitted:
    revision: int
    session_id: str
    fact: AgentCampaignFact


OperationFact = OperationCommitted | FleetOperationCommitted
JournalEntry = (
    OperationFact
    | RunEventCommitted
    | EffectReconciliationCommitted
    | EffectAcknowledgementCommitted
    | AgentCampaignCommitted
)
