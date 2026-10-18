import type { CausalityRevision } from "@/domain/concurrency/CausalityClock";
import type { FleetRunDetail, FleetRunState } from "@generated/models";
import { isFleetRunTerminal } from "@/domain/fleet/runLifecycle";

export interface LocalRunObservation {
  readonly run: FleetRunState;
  readonly causalityRevision: CausalityRevision;
  /** Durable Journal cut for event-derived observations; null for mutation-local observations. */
  readonly journalRevision: number | null;
}

export interface FleetRunOwnershipProjection {
  readonly activeRun: FleetRunState | null;
  /** Terminal state from the locally observed execution handoff; not global latest-history truth. */
  readonly completedLocalRun: FleetRunState | null;
}

export interface FleetRunStatusProjection extends FleetRunOwnershipProjection {
  readonly activeRunDetail: FleetRunDetail | null;
  readonly completedLocalRunDetail: FleetRunDetail | null;
}

interface OwnershipInput {
  readonly liveRun: LocalRunObservation | null;
  readonly observedActiveDetail: FleetRunDetail | null;
  /** Whether the fleet snapshot durably asserted its active-run projection, including "none". */
  readonly observedActiveKnown: boolean;
  readonly observedCausalityRevision: CausalityRevision | null;
  /** Durable Journal cut that produced observedActiveDetail / observed "none". */
  readonly observedJournalRevision: number | null;
  /** Durable terminal event/mutation evidence retained across detail-read failure. */
  readonly localTerminalRun: FleetRunState | null;
}

interface DetailInput extends FleetRunOwnershipProjection {
  /** Terminal enrichment read; active detail remains owned by the Fleet snapshot projection. */
  readonly completedRunDetail: FleetRunDetail | null;
  readonly observedActiveDetail: FleetRunDetail | null;
}

/**
 * Project execution identity without letting detail availability become a lifecycle owner.
 *
 * Backend active_fleet_run remains durable truth. liveRun is only a causally ordered local
 * event/mutation overlay. Event observations and Fleet snapshots carry the backend Journal
 * watermark so same-identity lifecycle facts are ordered by the durable owner, not by a
 * frontend status rank. Mutation-local observations use the frontend causality clock until a
 * later durable snapshot is observed.
 */
export function projectFleetRunOwnership({
  liveRun,
  observedActiveDetail,
  observedActiveKnown,
  observedCausalityRevision,
  observedJournalRevision,
  localTerminalRun,
}: OwnershipInput): FleetRunOwnershipProjection {
  const observedActiveRun = observedActiveDetail !== null && !isFleetRunTerminal(observedActiveDetail.run)
    ? observedActiveDetail.run
    : null;
  const localRun = liveRun?.run ?? null;
  const terminalEvidence = localTerminalRun !== null && isFleetRunTerminal(localTerminalRun)
    ? localTerminalRun
    : localRun !== null && isFleetRunTerminal(localRun)
      ? localRun
      : null;
  const localActiveRun = localRun !== null && !isFleetRunTerminal(localRun) ? localRun : null;
  const observedRunWasTerminalizedLocally = Boolean(
    observedActiveRun && terminalEvidence?.fleet_run_id === observedActiveRun.fleet_run_id,
  );
  const durableObservedActive = observedRunWasTerminalizedLocally ? null : observedActiveRun;
  const durableSnapshotSupersedesEventObservation = localActiveRun !== null
    && liveRun?.journalRevision !== null
    && liveRun?.journalRevision !== undefined
    && observedActiveKnown
    && observedJournalRevision !== null
    && observedJournalRevision >= liveRun.journalRevision;
  const causallyObservedDifferentIdentity = localActiveRun !== null
    && liveRun !== null
    && liveRun.journalRevision === null
    && observedActiveKnown
    && observedCausalityRevision !== null
    && observedCausalityRevision >= liveRun.causalityRevision
    && durableObservedActive?.fleet_run_id !== localActiveRun.fleet_run_id;
  const activeRun = durableSnapshotSupersedesEventObservation || causallyObservedDifferentIdentity
    ? durableObservedActive
    : localActiveRun ?? durableObservedActive;
  const completedLocalRun = activeRun === null ? terminalEvidence : null;
  return { activeRun, completedLocalRun };
}


/** A later durable active identity ends the lifetime of an older local completion handoff. */
export function retainCompletedLocalRun(
  current: FleetRunState | null,
  durableActiveRunId: string | null,
): FleetRunState | null {
  if (current === null || durableActiveRunId === null) return current;
  return current.fleet_run_id === durableActiveRunId ? current : null;
}

/** Bind enrichment only when its Run identity exactly matches the lifecycle projection. */
export function bindFleetRunDetails({
  activeRun,
  completedLocalRun,
  completedRunDetail,
  observedActiveDetail,
}: DetailInput): FleetRunStatusProjection {
  const activeRunId = activeRun?.fleet_run_id ?? null;
  const activeRunDetail = activeRunId !== null
    && observedActiveDetail?.run.fleet_run_id === activeRunId
    && !isFleetRunTerminal(observedActiveDetail.run)
      ? observedActiveDetail
      : null;
  const completedLocalRunDetail = completedLocalRun !== null
    && completedRunDetail?.run.fleet_run_id === completedLocalRun.fleet_run_id
    && isFleetRunTerminal(completedRunDetail.run)
      ? completedRunDetail
      : null;
  return { activeRun, activeRunDetail, completedLocalRun, completedLocalRunDetail };
}
