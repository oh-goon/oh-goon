import { describe, expect, it } from "vitest";

import { FleetRunStatus } from "@generated/enums";
import type { FleetRunDetail, FleetRunState } from "@generated/models";
import { bindFleetRunDetails, projectFleetRunOwnership, retainCompletedLocalRun } from "@/app/fleetRun/runStatusProjection";

function run(fleetRunId: string, status: FleetRunState["status"]): FleetRunState {
  return { fleet_run_id: fleetRunId, status } as FleetRunState;
}

function detail(fleetRunId: string, status: FleetRunState["status"]): FleetRunDetail {
  return { run: run(fleetRunId, status) } as FleetRunDetail;
}

function observed(
  live: FleetRunState | null,
  observedActiveDetail: FleetRunDetail | null,
  localTerminalRun: FleetRunState | null,
  liveRevision = 1,
  snapshotRevision = 1,
  liveJournalRevision: number | null = null,
  snapshotJournalRevision: number | null = null,
) {
  return projectFleetRunOwnership({
    liveRun: live === null ? null : { run: live, causalityRevision: liveRevision, journalRevision: liveJournalRevision },
    observedActiveDetail,
    observedActiveKnown: true,
    observedCausalityRevision: snapshotRevision,
    observedJournalRevision: snapshotJournalRevision,
    localTerminalRun,
  });
}

describe("Fleet Run status ownership", () => {
  it("revokes an old local completion handoff when durable truth owns a different active Run", () => {
    const completed = run("run-a", FleetRunStatus.COMPLETED);
    expect(retainCompletedLocalRun(completed, "run-b")).toBeNull();
    expect(retainCompletedLocalRun(completed, "run-a")).toBe(completed);
    expect(retainCompletedLocalRun(completed, null)).toBe(completed);
  });

  it("fails closed if a backend active snapshot contains terminal state", () => {
    expect(observed(null, detail("run-a", FleetRunStatus.CANCELLED), null)).toEqual({ activeRun: null, completedLocalRun: null });
  });

  it("never exposes a terminal live observation as active execution", () => {
    const terminal = run("run-a", FleetRunStatus.COMPLETED);
    expect(observed(terminal, detail("run-a", FleetRunStatus.RUNNING), null)).toEqual({ activeRun: null, completedLocalRun: terminal });
  });

  it("does not let terminal evidence for an old Run suppress a newer backend-active Run", () => {
    const terminalEvidence = run("run-a", FleetRunStatus.CANCELLED);
    const backend = detail("run-b", FleetRunStatus.RUNNING);
    expect(observed(null, backend, terminalEvidence)).toEqual({ activeRun: backend.run, completedLocalRun: null });
  });

  it("suppresses a stale backend-active projection after the watcher proves that exact Run terminal", () => {
    const terminal = run("run-a", FleetRunStatus.COMPLETED);
    const staleObserved = detail("run-a", FleetRunStatus.RUNNING);
    expect(observed(null, staleObserved, terminal)).toEqual({ activeRun: null, completedLocalRun: terminal });
  });

  it("keeps a newer local admission over an older fleet snapshot", () => {
    const local = run("run-b", FleetRunStatus.QUEUED);
    const staleBackend = detail("run-a", FleetRunStatus.RUNNING);
    expect(observed(local, staleBackend, null, 2, 1).activeRun).toBe(local);
  });

  it("lets an equally-new durable snapshot replace a stale local Run with another active identity", () => {
    const staleLocal = run("run-a", FleetRunStatus.RUNNING);
    const backend = detail("run-b", FleetRunStatus.RUNNING);
    expect(observed(staleLocal, backend, null, 2, 2).activeRun).toBe(backend.run);
  });

  it("lets an equally-new durable snapshot prove that no Run remains active", () => {
    const staleLocal = run("run-a", FleetRunStatus.RUNNING);
    expect(observed(staleLocal, null, null, 2, 2)).toEqual({ activeRun: null, completedLocalRun: null });
  });


  it("orders same-identity lifecycle facts by the backend Journal watermark", () => {
    const localRunning = run("run-a", FleetRunStatus.RUNNING);
    const durableCancelling = detail("run-a", FleetRunStatus.CANCELLING);
    expect(observed(localRunning, durableCancelling, null, 2, 2, 40, 41).activeRun).toBe(durableCancelling.run);
    expect(observed(run("run-a", FleetRunStatus.CANCELLING), detail("run-a", FleetRunStatus.RUNNING), null, 2, 2, 42, 41).activeRun?.status).toBe(FleetRunStatus.CANCELLING);
  });

  it("lets a later durable Journal cut prove that an event-observed Run is no longer active", () => {
    const localRunning = run("run-a", FleetRunStatus.RUNNING);
    expect(observed(localRunning, null, null, 2, 2, 40, 41)).toEqual({ activeRun: null, completedLocalRun: null });
  });

  it("keeps the local lifecycle overlay when backend and local identity are the same", () => {
    const cancelling = run("run-a", FleetRunStatus.CANCELLING);
    expect(observed(cancelling, detail("run-a", FleetRunStatus.RUNNING), null, 2, 2).activeRun).toBe(cancelling);
  });

  it("does not resurrect a terminal Run when final detail loading fails", () => {
    const terminal = run("run-a", FleetRunStatus.CANCELLED);
    const ownership = observed(null, detail("run-a", FleetRunStatus.CANCELLING), terminal);
    expect(bindFleetRunDetails({
      ...ownership,
      completedRunDetail: null,
      observedActiveDetail: detail("run-a", FleetRunStatus.CANCELLING),
    })).toEqual({
      activeRun: null,
      activeRunDetail: null,
      completedLocalRun: terminal,
      completedLocalRunDetail: null,
    });
  });

  it("binds detail only to the exact active Run identity", () => {
    const active = run("run-b", FleetRunStatus.RUNNING);
    const staleCompleted = detail("run-a", FleetRunStatus.COMPLETED);
    const backend = detail("run-b", FleetRunStatus.RUNNING);
    expect(bindFleetRunDetails({
      activeRun: active,
      completedLocalRun: null,
      completedRunDetail: staleCompleted,
      observedActiveDetail: backend,
    })).toEqual({
      activeRun: active,
      activeRunDetail: backend,
      completedLocalRun: null,
      completedLocalRunDetail: null,
    });
  });

  it("never lets the run-detail endpoint become a second active-detail owner", () => {
    const active = run("run-a", FleetRunStatus.RUNNING);
    const duplicateActiveDetail = detail("run-a", FleetRunStatus.RUNNING);
    expect(bindFleetRunDetails({
      activeRun: active,
      completedLocalRun: null,
      completedRunDetail: duplicateActiveDetail,
      observedActiveDetail: null,
    }).activeRunDetail).toBeNull();
  });

  it("never binds terminal detail beneath a non-terminal active lifecycle owner", () => {
    const active = run("run-a", FleetRunStatus.RUNNING);
    const terminalDetail = detail("run-a", FleetRunStatus.COMPLETED);
    expect(bindFleetRunDetails({
      activeRun: active,
      completedLocalRun: null,
      completedRunDetail: terminalDetail,
      observedActiveDetail: null,
    }).activeRunDetail).toBeNull();
  });

  it("binds terminal detail only to the completed local Run identity", () => {
    const completed = run("run-a", FleetRunStatus.CANCELLED);
    const terminalDetail = detail("run-a", FleetRunStatus.CANCELLED);
    expect(bindFleetRunDetails({
      activeRun: null,
      completedLocalRun: completed,
      completedRunDetail: terminalDetail,
      observedActiveDetail: null,
    }).completedLocalRunDetail).toBe(terminalDetail);
  });
});
