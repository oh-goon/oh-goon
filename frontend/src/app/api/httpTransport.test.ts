import { afterEach, describe, expect, it, vi } from "vitest";

import { executeOperation, readOperation } from "@/app/api/httpTransport";
import { operations } from "@generated/operations";

const healthOperation = operations.health.operationId;

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("httpTransport retries", () => {
  it("keeps an explicitly budgeted long request open beyond the ordinary timeout", async () => {
    vi.useFakeTimers();
    let respond: (response: Response) => void = () => { throw new Error("No request"); };
    let signal: AbortSignal | null = null;
    const fetchMock = vi.fn((_url: string, init: RequestInit) => {
      signal = init.signal ?? null;
      return new Promise<Response>((resolve) => { respond = resolve; });
    });
    vi.stubGlobal("fetch", fetchMock);
    const pending = executeOperation(
      operations.get_fleet_run_enforcer_receipt.operationId,
      { path: { fleet_run_id: "long-request" } }, undefined, { timeoutMs: 60_000 },
    );
    await vi.advanceTimersByTimeAsync(10_001);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(signal!.aborted).toBe(false);
    respond(new Response("receipt", { status: 200 }));
    await expect(pending).resolves.toBe("receipt");
  });

  it("does not issue a retry after the caller aborts during backoff", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("offline"));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    const pending = readOperation(healthOperation, {}, { signal: controller.signal });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    controller.abort();

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    await vi.runAllTimersAsync();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not dispatch when the caller signal is already aborted", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    controller.abort();

    await expect(readOperation(healthOperation, {}, { signal: controller.signal })).rejects.toMatchObject({
      name: "AbortError",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
