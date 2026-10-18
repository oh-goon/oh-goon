import { afterEach, describe, expect, it, vi } from "vitest";

import { FleetRunEventType, FleetRunStatus } from "@generated/enums";
import type { FleetRunEventBatch, FleetRunState } from "@generated/models";
import type { ApiClient } from "@/domain/api/ApiClient";
import { ReadController } from "@/domain/concurrency/ReadController";
import { watchFleetRun } from "@/app/fleetRun/watchFleetRun";

function run(id: string, status: FleetRunState["status"]): FleetRunState {
  return { fleet_run_id: id, status } as FleetRunState;
}

function batch(state: FleetRunState, events: FleetRunEventBatch["events"] = []): FleetRunEventBatch {
  return { run: state, events, next_sequence: events.length, journal_revision: 10 };
}

function callbacks() {
  return {
    onBatch: vi.fn(),
    onProgress: vi.fn(),
    onError: vi.fn(),
    onInvariant: vi.fn(),
    onHealthy: vi.fn(),
    onTerminal: vi.fn(),
  };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("watchFleetRun ownership", () => {
  it("continues watching the same Run after causal invalidation supersedes an in-flight event read", async () => {
    vi.useFakeTimers();
    const reads = new ReadController();
    let finishFirst!: (value: FleetRunEventBatch) => void;
    let calls = 0;
    const client = {
      fleetRunEvents: vi.fn(async () => {
        calls += 1;
        if (calls === 1) return await new Promise<FleetRunEventBatch>((resolve) => { finishFirst = resolve; });
        return batch(run("run-a", FleetRunStatus.RUNNING));
      }),
    } as unknown as ApiClient;
    const stop = watchFleetRun(client, reads, "run-a", "progress failed", callbacks());

    await Promise.resolve();
    reads.causality.observe(1);
    reads.invalidate("fleet-run-events", 1);
    finishFirst(batch(run("run-a", FleetRunStatus.RUNNING)));
    await Promise.resolve();
    await Promise.resolve();

    await vi.advanceTimersByTimeAsync(400);
    expect(client.fleetRunEvents).toHaveBeenCalledTimes(2);
    stop();
  });

  it("invalidates derived projections for every durable Run event, including cancellation requests", async () => {
    vi.useFakeTimers();
    const reads = new ReadController();
    const observed = callbacks();
    const client = {
      fleetRunEvents: vi.fn(async () => batch(
        run("run-a", FleetRunStatus.CANCELLING),
        [{ fleet_run_id: "run-a", sequence: 1, event_type: FleetRunEventType.RUN_CANCEL_REQUESTED }] as FleetRunEventBatch["events"],
      )),
    } as unknown as ApiClient;
    const stop = watchFleetRun(client, reads, "run-a", "progress failed", observed);

    await Promise.resolve();
    await Promise.resolve();

    expect(observed.onProgress).toHaveBeenCalledTimes(1);
    expect(observed.onBatch).toHaveBeenCalledWith(
      expect.objectContaining({ fleet_run_id: "run-a", status: FleetRunStatus.CANCELLING }),
      0,
      10,
    );
    stop();
  });

  it("does not restart a superseded poll after the watcher has been stopped", async () => {
    vi.useFakeTimers();
    const reads = new ReadController();
    let finishFirst!: (value: FleetRunEventBatch) => void;
    const client = {
      fleetRunEvents: vi.fn(() => new Promise<FleetRunEventBatch>((resolve) => { finishFirst = resolve; })),
    } as unknown as ApiClient;
    const stop = watchFleetRun(client, reads, "run-a", "progress failed", callbacks());

    await Promise.resolve();
    stop();
    finishFirst(batch(run("run-a", FleetRunStatus.RUNNING)));
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(800);

    expect(client.fleetRunEvents).toHaveBeenCalledTimes(1);
  });
  it("rejects an event batch that crosses the requested Run identity", async () => {
    vi.useFakeTimers();
    const reads = new ReadController();
    const observed = callbacks();
    const client = {
      fleetRunEvents: vi.fn(async () => batch(run("run-b", FleetRunStatus.RUNNING))),
    } as unknown as ApiClient;
    const stop = watchFleetRun(client, reads, "run-a", "progress failed", observed);

    await Promise.resolve();
    await Promise.resolve();

    expect(observed.onBatch).not.toHaveBeenCalled();
    expect(observed.onError).toHaveBeenCalledTimes(1);
    expect(observed.onInvariant).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(client.fleetRunEvents).toHaveBeenCalledTimes(1);
    stop();
  });

  it("rejects a batch that replays an event at or before the owned cursor", async () => {
    vi.useFakeTimers();
    const reads = new ReadController();
    const observed = callbacks();
    let calls = 0;
    const firstEvent = { fleet_run_id: "run-a", sequence: 1, event_type: FleetRunEventType.RUN_CANCEL_REQUESTED };
    const client = {
      fleetRunEvents: vi.fn(async () => {
        calls += 1;
        if (calls === 1) return { run: run("run-a", FleetRunStatus.RUNNING), events: [firstEvent], next_sequence: 1, journal_revision: 10 };
        return { run: run("run-a", FleetRunStatus.RUNNING), events: [firstEvent], next_sequence: 1, journal_revision: 10 };
      }),
    } as unknown as ApiClient;
    const stop = watchFleetRun(client, reads, "run-a", "progress failed", observed);

    await Promise.resolve();
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(400);

    expect(observed.onBatch).toHaveBeenCalledTimes(1);
    expect(observed.onError).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(client.fleetRunEvents).toHaveBeenCalledTimes(2);
    stop();
  });

  it("rejects a batch that skips an event sequence", async () => {
    vi.useFakeTimers();
    const reads = new ReadController();
    const observed = callbacks();
    let calls = 0;
    const first = { fleet_run_id: "run-a", sequence: 1, event_type: FleetRunEventType.RUN_STARTED };
    const skipped = { fleet_run_id: "run-a", sequence: 3, event_type: FleetRunEventType.RUN_CANCEL_REQUESTED };
    const client = {
      fleetRunEvents: vi.fn(async () => {
        calls += 1;
        if (calls === 1) return { run: run("run-a", FleetRunStatus.RUNNING), events: [first], next_sequence: 1, journal_revision: 10 };
        return { run: run("run-a", FleetRunStatus.CANCELLING), events: [skipped], next_sequence: 3, journal_revision: 11 };
      }),
    } as unknown as ApiClient;
    const stop = watchFleetRun(client, reads, "run-a", "progress failed", observed);

    await Promise.resolve();
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(400);
    await Promise.resolve();

    expect(observed.onBatch).toHaveBeenCalledTimes(1);
    expect(observed.onError).toHaveBeenCalledTimes(1);
    stop();
  });

  it("rejects an empty batch that advances the event cursor", async () => {
    vi.useFakeTimers();
    const reads = new ReadController();
    const observed = callbacks();
    const client = {
      fleetRunEvents: vi.fn(async () => ({ run: run("run-a", FleetRunStatus.RUNNING), events: [], next_sequence: 2, journal_revision: 10 } as FleetRunEventBatch)),
    } as unknown as ApiClient;
    const stop = watchFleetRun(client, reads, "run-a", "progress failed", observed);

    await Promise.resolve();
    await Promise.resolve();

    expect(observed.onBatch).not.toHaveBeenCalled();
    expect(observed.onError).toHaveBeenCalledTimes(1);
    stop();
  });

  it("fails closed when an event batch has no durable Journal watermark", async () => {
    vi.useFakeTimers();
    const reads = new ReadController();
    const observed = callbacks();
    const client = {
      fleetRunEvents: vi.fn(async () => ({ run: run("run-a", FleetRunStatus.RUNNING), events: [], next_sequence: 0 })),
    } as unknown as ApiClient;
    const stop = watchFleetRun(client, reads, "run-a", "progress failed", observed);

    await Promise.resolve();
    await Promise.resolve();

    expect(observed.onBatch).not.toHaveBeenCalled();
    expect(observed.onError).toHaveBeenCalledTimes(1);
    stop();
  });

  it("invalidates derived projections when terminal state is observed even with an empty event batch", async () => {
    vi.useFakeTimers();
    const reads = new ReadController();
    const observed = callbacks();
    const client = {
      fleetRunEvents: vi.fn(async () => batch(run("run-a", FleetRunStatus.CANCELLED))),
    } as unknown as ApiClient;
    const stop = watchFleetRun(client, reads, "run-a", "progress failed", observed);

    await Promise.resolve();
    await Promise.resolve();

    expect(observed.onProgress).toHaveBeenCalledTimes(1);
    expect(observed.onTerminal).toHaveBeenCalledWith(expect.objectContaining({ status: FleetRunStatus.CANCELLED }));
    stop();
  });

});
