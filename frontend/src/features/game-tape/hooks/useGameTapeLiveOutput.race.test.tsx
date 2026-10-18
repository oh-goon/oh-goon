import { act, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiClientProvider } from "@/app/ApiClientProvider";
import { ReadController } from "@/domain/concurrency/ReadController";
import type { ReadResource } from "@/domain/reads/ReadResource";
import { useGameTapeLiveOutput } from "@/features/game-tape/hooks/useGameTapeLiveOutput";
import type { GameTapeLiveOutputBatch } from "@generated/models";
import { InMemoryApiClient } from "@test/InMemoryApiClient";

interface Deferred<Value> {
  readonly promise: Promise<Value>;
  resolve(value: Value): void;
}

function deferred<Value>(): Deferred<Value> {
  let resolve!: (value: Value) => void;
  const promise = new Promise<Value>((complete) => { resolve = complete; });
  return { promise, resolve };
}

function liveBatch(nextSequence: number): GameTapeLiveOutputBatch {
  return {
    fleet_run_id: "run-a",
    state: "ACTIVE",
    available: true,
    expires_at: null,
    chunks: [{
      sequence: nextSequence,
      repository_id: "repo-a",
      step_id: "step-a",
      attempt: 1,
      stream: "stdout",
      line_start: nextSequence,
      line_end: nextSequence,
      text: `line ${nextSequence}`,
      emitted_at: "2026-10-04T12:00:00Z",
    }],
    failure_anchors: [],
    failure_navigation_state: "AVAILABLE",
    next_sequence: nextSequence,
    retained_from_sequence: nextSequence,
    dropped_chunk_count: 0,
  } as GameTapeLiveOutputBatch;
}

describe("useGameTapeLiveOutput accepted-generation ownership", () => {
  it("keeps the accepted cursor when a superseded same-run request returns late", async () => {
    const reads = new ReadController();
    const client = new InMemoryApiClient();
    const first = deferred<GameTapeLiveOutputBatch>();
    const second = deferred<GameTapeLiveOutputBatch>();
    const third = deferred<GameTapeLiveOutputBatch>();
    const responses = [first, second, third];
    const liveOutput = vi.spyOn(client, "gameTapeLiveOutput").mockImplementation(async () => {
      const response = responses.shift();
      if (response === undefined) throw new Error("unexpected live-output read");
      return response.promise;
    });
    const observed: { current: ReadResource<GameTapeLiveOutputBatch> | null } = { current: null };

    function Probe() {
      observed.current = useGameTapeLiveOutput("run-a", true, false);
      return null;
    }

    const view = render(
      <ApiClientProvider client={client} readController={reads}>
        <Probe />
      </ApiClientProvider>,
    );
    await act(async () => { await Promise.resolve(); });
    expect(liveOutput).toHaveBeenCalledTimes(1);
    expect(liveOutput.mock.calls[0]?.[1]).toBe(0);

    await act(async () => {
      reads.invalidate("game-tape-live-output");
      await Promise.resolve();
    });
    expect(liveOutput).toHaveBeenCalledTimes(2);
    expect(liveOutput.mock.calls[1]?.[1]).toBe(0);

    await act(async () => {
      second.resolve(liveBatch(20));
      await second.promise;
      await Promise.resolve();
    });
    expect(observed.current?.data?.next_sequence).toBe(20);

    await act(async () => {
      first.resolve(liveBatch(10));
      await first.promise;
      await Promise.resolve();
    });
    expect(observed.current?.data?.next_sequence).toBe(20);

    await act(async () => {
      reads.invalidate("game-tape-live-output");
      await Promise.resolve();
    });
    expect(liveOutput).toHaveBeenCalledTimes(3);
    expect(liveOutput.mock.calls[2]?.[1]).toBe(20);

    await act(async () => {
      third.resolve(liveBatch(21));
      await third.promise;
      await Promise.resolve();
    });
    view.unmount();
  });
});
