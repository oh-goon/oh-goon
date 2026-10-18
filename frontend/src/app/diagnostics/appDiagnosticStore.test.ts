import { describe, expect, it } from "vitest";

import { appDiagnosticStore } from "@/domain/diagnostics/appDiagnosticStore";
import { ApiError } from "@/domain/api/ApiError";

describe("appDiagnosticStore", () => {
  it("redacts sensitive values and deduplicates repeated handled errors", () => {
    appDiagnosticStore.clear();
    const error = new ApiError(
      "Failed at C:\\Users\\alice\\repo with ?token=secret-value",
      503,
      "SERVICE_UNAVAILABLE",
      "INTERNAL",
      true,
      {},
      "request-fixture",
    );
    const first = appDiagnosticStore.record({ source: "GET /fleet", error, scope: "fleet", causalityRevision: 4 });
    const second = appDiagnosticStore.record({ source: "GET /fleet", error, scope: "fleet", causalityRevision: 4 });

    expect(second.id).toBe(first.id);
    expect(second.occurrences).toBe(2);
    expect(second.message).not.toContain("alice");
    expect(second.message).not.toContain("secret-value");
    expect(second.requestId).toBe("request-fixture");
    expect(second.scope).toBe("fleet");
    expect(second.causalityRevision).toBe(4);
    expect(appDiagnosticStore.getSnapshot()).toHaveLength(1);
  });
});
