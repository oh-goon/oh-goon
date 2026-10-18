import { describe, expect, it } from "vitest";

import { beginRead, completeRead, failRead, idleReadResource } from "@/domain/reads/ReadResource";

const failure = {
  diagnosticId: "diag-1",
  message: "refresh failed",
  recoverable: true,
} as const;

describe("ReadResource", () => {
  it("distinguishes initial loading from background refresh", () => {
    expect(beginRead(idleReadResource<string>()).status).toBe("read-loading");

    const fresh = completeRead("snapshot", "rev-1", 3, "2026-08-10T16:00:00.000Z");
    const refreshing = beginRead(fresh);
    expect(refreshing).toMatchObject({ status: "read-refreshing", data: "snapshot", revision: "rev-1" });
  });

  it("retains authoritative previous data when a refresh fails", () => {
    const fresh = completeRead("snapshot", "rev-1", 3, "2026-08-10T16:00:00.000Z");
    expect(failRead(fresh, failure)).toEqual({
      status: "read-stale",
      data: "snapshot",
      revision: "rev-1",
      causalityRevision: 3,
      fetchedAt: "2026-08-10T16:00:00.000Z",
      failure,
    });
  });

  it("reports an error when no prior usable value exists", () => {
    expect(failRead(idleReadResource<string>(), failure)).toEqual({
      status: "read-error",
      data: null,
      revision: null,
      causalityRevision: null,
      fetchedAt: null,
      failure,
    });
  });
});
