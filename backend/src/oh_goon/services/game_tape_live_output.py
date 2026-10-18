from __future__ import annotations

from oh_goon.core.game_tape_live_failure_anchor import project_live_failure_anchors
from oh_goon.core.models.diagnostic import DiagnosticEvent
from oh_goon.core.models.game_tape_live_output import GameTapeLiveOutputBatch
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.ports.diagnostics import DiagnosticEmitter
from oh_goon.core.vocabulary import GameTapeFailureNavigationState
from oh_goon.runtime.game_tape_live_output import GameTapeLiveOutputStore


def read_game_tape_live_output(
    store: GameTapeLiveOutputStore,
    detail: FleetRunDetail,
    *,
    after: int,
    diagnostics: DiagnosticEmitter | None = None,
) -> GameTapeLiveOutputBatch:
    batch, retained = store.snapshot_cut(detail.run.fleet_run_id, after)
    if not batch.available:
        return batch

    try:
        anchors = project_live_failure_anchors(detail.game_tape, retained)
    except Exception as exc:
        # Failure navigation is derived. Preserve the authoritative transient batch
        # when enrichment itself fails and record why the projection degraded.
        if diagnostics is not None:
            diagnostics.emit(
                DiagnosticEvent.from_exception(
                    exc,
                    severity="WARNING",
                    source="game_tape_failure_navigation",
                    owner="GameTapeLiveOutputProjection",
                    operation="project_failure_anchors",
                    execution_affected=False,
                    diagnostic_capability_affected="failure_navigation",
                    failure_class="diagnostic_degraded",
                    fleet_run_id=detail.run.fleet_run_id,
                )
            )
        return batch.model_copy(
            update={"failure_navigation_state": GameTapeFailureNavigationState.DEGRADED}
        )
    return batch.model_copy(
        update={
            "failure_anchors": anchors,
            "failure_navigation_state": GameTapeFailureNavigationState.AVAILABLE,
        }
    )
