import { afterEach, describe, expect, it } from "vitest";

import { operationHref } from "@/domain/api/operationHref";
import { operations } from "@generated/operations";

const healthMethod = Object.getOwnPropertyDescriptor(operations.health, "method")!;
afterEach(() => { Object.defineProperty(operations.health, "method", healthMethod); });

describe("generated operation navigation", () => {
  it("preserves encoded path identity and generated query parameters", () => {
    expect(operationHref(operations.fleet_run_events.operationId, {
      path: { fleet_run_id: "recorded run ?#snow" }, query: { after: 7 },
    })).toBe("/api/fleet/runs/recorded%20run%20%3F%23snow/events?after=7");
  });

  it("refuses navigation without a required path identity", () => {
    expect(() => operationHref(operations.fleet_run_events.operationId, { path: {} } as never)).toThrow("Missing path parameter");
  });

  it("refuses a contract changed to a write method instead of navigating with GET", () => {
    Object.defineProperty(operations.health, "method", { value: "POST" });
    expect(() => operationHref(operations.health.operationId, {})).toThrow("Browser navigation requires a GET operation");
  });
});
