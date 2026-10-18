import { recordReadFailure } from "@/app/reads/recordReadFailure";
import type { ApiClient } from "@/domain/api/ApiClient";
import type { FleetRunEventBatch, FleetRunState } from "@generated/models";
import type { ReadController } from "@/domain/concurrency/ReadController";
import type { CausalityRevision } from "@/domain/concurrency/CausalityClock";
import { isFleetRunTerminal, runPollDelay } from "@/domain/fleet/runLifecycle";

const RUN_POLL_MS = 400;

export interface FleetRunWatchCallbacks {
  onBatch(run: FleetRunState, causalityRevision: CausalityRevision, journalRevision: number): void;
  onProgress(): void;
  onError(): void;
  onInvariant(): void;
  onHealthy(): void;
  onTerminal(run: FleetRunState): void;
}

export function watchFleetRun(
  client: ApiClient,
  reads: ReadController,
  runId: string,
  errorMessage: string,
  callbacks: FleetRunWatchCallbacks,
): () => void {
  let stopped = false;
  let sequence = 0;
  let failures = 0;
  let timer: number | undefined;
  let inFlight = false;

  const poll = async (): Promise<void> => {
    if (stopped || inFlight || document.visibilityState !== "visible") return;
    inFlight = true;
    try {
      const execution = await reads.execute(
        "fleet-run-events",
        runId,
        (signal) => client.fleetRunEvents(runId, sequence, { signal }),
      );
      if (stopped) return;
      if (execution.outcome === "superseded") {
        // A saved mutation causally invalidates run-history reads, including this
        // event scope. That must supersede the pre-write response, but it must
        // not silently end the watcher while the same Run is still owned here.
        inFlight = false;
        timer = window.setTimeout(() => void poll(), RUN_POLL_MS);
        return;
      }
      const batch = execution.value;
      const journalRevision = requireOwnedEventBatch(runId, sequence, batch);
      sequence = batch.next_sequence;
      failures = 0;
      callbacks.onHealthy();
      callbacks.onBatch(batch.run, execution.causalityRevision, journalRevision);
      const progressed = batch.events.length > 0;
      const terminal = isFleetRunTerminal(batch.run);
      if (progressed || terminal) callbacks.onProgress();
      if (terminal) {
        callbacks.onTerminal(batch.run);
        inFlight = false;
      } else {
        inFlight = false;
        timer = window.setTimeout(() => void poll(), RUN_POLL_MS);
      }
    } catch (error) {
      inFlight = false;
      if (stopped) return;
      recordReadFailure(`GET /fleet/runs/${runId}/events`, error, errorMessage, "fleet-run-events", reads.causality.current());
      callbacks.onError();
      if (error instanceof FleetRunWatchInvariantError) {
        // Wrong-identity/cursor data is a protocol ownership breach, not a
        // transient transport failure. Retrying the same watch could consume
        // unowned history. Ask the owner to re-read durable fleet truth, but
        // keep this watcher stopped for the current Run identity.
        callbacks.onInvariant();
        stopped = true;
        return;
      }
      failures += 1;
      timer = window.setTimeout(() => void poll(), runPollDelay(failures, RUN_POLL_MS));
    }
  };

  const onVisibilityChange = () => {
    if (document.visibilityState !== "visible" || stopped || inFlight) return;
    if (timer !== undefined) window.clearTimeout(timer);
    timer = undefined;
    void poll();
  };
  const onOnline = () => {
    if (document.visibilityState !== "visible" || stopped || inFlight) return;
    if (timer !== undefined) window.clearTimeout(timer);
    timer = undefined;
    void poll();
  };
  document.addEventListener("visibilitychange", onVisibilityChange);
  window.addEventListener("online", onOnline);

  void poll();
  return () => {
    stopped = true;
    reads.invalidate("fleet-run-events");
    document.removeEventListener("visibilitychange", onVisibilityChange);
    window.removeEventListener("online", onOnline);
    if (timer !== undefined) window.clearTimeout(timer);
  };
}


class FleetRunWatchInvariantError extends Error {}

function requireOwnedEventBatch(runId: string, after: number, batch: FleetRunEventBatch): number {
  const journalRevision = batch.journal_revision;
  if (!Number.isInteger(journalRevision) || journalRevision < 0) {
    throw new FleetRunWatchInvariantError(`Fleet Run event batch is missing a valid Journal watermark for ${runId}`);
  }
  if (batch.run.fleet_run_id !== runId) {
    throw new FleetRunWatchInvariantError(`Fleet Run event batch identity mismatch: requested ${runId}, received ${batch.run.fleet_run_id}`);
  }
  if (batch.events.length === 0) {
    if (batch.next_sequence !== after) {
      throw new FleetRunWatchInvariantError(`Fleet Run event cursor advanced without evidence for ${runId}: ${after} -> ${batch.next_sequence}`);
    }
    return journalRevision;
  }
  for (const [index, event] of batch.events.entries()) {
    const expectedSequence = after + index + 1;
    if (event.fleet_run_id !== runId || event.sequence !== expectedSequence) {
      throw new FleetRunWatchInvariantError(`Fleet Run event batch crossed the owned cursor or Run identity for ${runId}`);
    }
  }
  const lastSequence = batch.events[batch.events.length - 1]!.sequence;
  if (batch.next_sequence !== lastSequence) {
    throw new FleetRunWatchInvariantError(`Fleet Run event cursor does not match returned evidence for ${runId}: ${batch.next_sequence} != ${lastSequence}`);
  }
  return journalRevision;
}
