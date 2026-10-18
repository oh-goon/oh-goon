import { describe, expect, it } from "vitest";

import { formatAttemptResourceEnvelope } from "@/features/game-tape/gameTapeAttemptResources";

describe("formatAttemptResourceEnvelope", () => {
  it("renders omitted limits as executor-available instead of inventing fixed caps", () => {
    expect(formatAttemptResourceEnvelope({
      cpu_millis: null,
      memory_bytes: null,
      max_processes: null,
      cpu_usage_usec: 17,
      memory_peak_bytes: 1024,
      pids_peak: 2,
      oom_kill_count: 0,
      policy_id: "plan-resource-policy:fixture",
    }, "executor available")).toBe("executor available CPU · executor available RAM");
  });

  it("renders explicit limits without changing their values", () => {
    expect(formatAttemptResourceEnvelope({
      cpu_millis: 1500,
      memory_bytes: 512 * 1024 * 1024,
      max_processes: 32,
      cpu_usage_usec: null,
      memory_peak_bytes: null,
      pids_peak: null,
      oom_kill_count: null,
      policy_id: "plan-resource-policy:fixture",
    }, "executor available")).toBe("1500m CPU · 512.0 MiB RAM · 32 processes");
  });
});
