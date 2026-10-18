from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from oh_goon.core.models.runtime_supervision import (
    RuntimeObservation,
    RuntimeSupervisionPolicy,
    SemanticProgress,
    WatchdogDecision,
)
from oh_goon.runtime.watchdog import accept_semantic_progress, evaluate_watchdog

HeartbeatProvider = Callable[[str, int], datetime | None]
LeaseValidityProvider = Callable[[str, int], bool]
SemanticProgressProvider = Callable[[str, int], SemanticProgress | None]
YieldRequestProvider = Callable[[str, int], datetime | None]
WatchdogObserver = Callable[[WatchdogDecision], None]


@dataclass(frozen=True, slots=True)
class CommandRuntimeSupervision:
    """Resolved supervision authority transported to one physical command execution.

    Providers expose canonical owner facts. Output/log activity is deliberately absent:
    it cannot renew heartbeat, lease, or semantic progress.
    """

    policy: RuntimeSupervisionPolicy
    generation: int
    heartbeat: HeartbeatProvider
    lease_valid: LeaseValidityProvider
    semantic_progress: SemanticProgressProvider
    yield_requested_at: YieldRequestProvider | None = None
    observe_decision: WatchdogObserver | None = None

    def __post_init__(self) -> None:
        if self.generation < 1:
            raise ValueError("runtime supervision generation must be positive")


@dataclass(slots=True)
class CommandWatchdog:
    supervision: CommandRuntimeSupervision
    execution_id: str
    started_at: datetime
    last_progress: SemanticProgress | None = None

    def evaluate(
        self, *, process_alive: bool, completed_at: datetime | None = None
    ) -> WatchdogDecision:
        candidate = self.supervision.semantic_progress(
            self.execution_id, self.supervision.generation
        )
        if candidate is not None:
            self.last_progress = accept_semantic_progress(
                self.last_progress,
                candidate,
                execution_id=self.execution_id,
                generation=self.supervision.generation,
            )
        heartbeat = self.supervision.heartbeat(self.execution_id, self.supervision.generation)
        lease_valid = self.supervision.lease_valid(self.execution_id, self.supervision.generation)
        yield_requested_at = (
            None
            if self.supervision.yield_requested_at is None
            else self.supervision.yield_requested_at(self.execution_id, self.supervision.generation)
        )
        observed_at = datetime.now(timezone.utc)
        observation = RuntimeObservation(
            execution_id=self.execution_id,
            generation=self.supervision.generation,
            started_at=self.started_at,
            observed_at=observed_at,
            last_heartbeat_at=heartbeat,
            lease_valid=lease_valid,
            process_alive=process_alive,
            last_progress=self.last_progress,
            completed_at=completed_at,
            yield_requested_at=yield_requested_at,
        )
        decision = evaluate_watchdog(self.supervision.policy, observation)
        if self.supervision.observe_decision is not None:
            self.supervision.observe_decision(decision)
        return decision
