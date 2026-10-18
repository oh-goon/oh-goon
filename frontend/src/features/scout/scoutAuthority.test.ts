import { describe, expect, it } from "vitest";

import { ApiError } from "@/domain/api/ApiError";
import { retainDataUnlessNotFound } from "@/domain/reads/retainDataOnError";
import { lastValidPageOffset } from "@/features/scout/scoutAuthority";

describe("Scout authority semantics", () => {
  it("clamps a moving population to the last valid page", () => {
    expect(lastValidPageOffset(205, 100)).toBe(200);
    expect(lastValidPageOffset(200, 100)).toBe(100);
    expect(lastValidPageOffset(0, 100)).toBe(0);
  });

  it("revokes retained evidence only for definitive NOT_FOUND", () => {
    expect(retainDataUnlessNotFound(new ApiError("missing", 404, "NOT_FOUND", "NOT_FOUND"))).toBe(false);
    expect(retainDataUnlessNotFound(new Error("temporary transport failure"))).toBe(true);
  });
});
