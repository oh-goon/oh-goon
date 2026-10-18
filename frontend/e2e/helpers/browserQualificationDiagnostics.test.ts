import { describe, expect, it } from "vitest";

import {
  boundedBrowserEvidence,
  classifyBrowserNavigationFailure,
  refineBrowserNavigationFailure,
} from "./browserQualificationDiagnostics";

describe("browser qualification diagnostics", () => {
  it("classifies Chromium network generation invalidation separately from navigation failure", () => {
    expect(classifyBrowserNavigationFailure(
      ["requestfailed:http://127.0.0.1:5173/src/main.tsx:net::ERR_NETWORK_CHANGED"],
      new Error("heading never appeared"),
    )).toBe("BROWSER_NETWORK_EPOCH_CHANGED");
    expect(classifyBrowserNavigationFailure([], new Error("ECONNREFUSED"))).toBe("BROWSER_FIXTURE_NAVIGATION_FAILED");
  });

  it("bounds browser evidence to the newest causal sample", () => {
    expect(boundedBrowserEvidence(["a", "b", "c"], 2)).toEqual(["b", "c"]);
  });

  it("separates an unavailable fixture transport from a healthy-origin navigation failure", () => {
    const failedProbe = { ok: false, statusCode: null, code: "ECONNREFUSED", elapsedMs: 3 };
    const healthyProbe = { ok: true, statusCode: 200, code: null, elapsedMs: 2 };
    expect(refineBrowserNavigationFailure("BROWSER_FIXTURE_NAVIGATION_FAILED", failedProbe))
      .toBe("BROWSER_TRANSPORT_UNAVAILABLE");
    expect(refineBrowserNavigationFailure("BROWSER_FIXTURE_NAVIGATION_FAILED", healthyProbe))
      .toBe("BROWSER_FIXTURE_NAVIGATION_FAILED");
    expect(refineBrowserNavigationFailure("BROWSER_NETWORK_EPOCH_CHANGED", failedProbe))
      .toBe("BROWSER_NETWORK_EPOCH_CHANGED");
  });
});
