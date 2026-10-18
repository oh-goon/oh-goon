import { afterEach, describe, expect, it, vi } from "vitest";

import { readOperation } from "@/app/api/httpTransport";
import { operations } from "@generated/operations";

const healthOperation = operations.health.operationId;

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("httpTransport retries", () => {
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
