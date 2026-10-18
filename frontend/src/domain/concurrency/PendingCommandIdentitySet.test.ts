import { describe, expect, it } from "vitest";

import { PendingCommandIdentitySet } from "@/domain/concurrency/PendingCommandIdentitySet";

function identities(...values: string[]) {
  let index = 0;
  return () => values[index++] ?? `generated-${index}`;
}

describe("PendingCommandIdentitySet", () => {
  it("retains each ambiguous command identity independently", () => {
    const pending = new PendingCommandIdentitySet(identities("run-a", "run-b", "run-c"));

    expect(pending.forCommand("command-a")).toBe("run-a");
    pending.settle("command-a", { outcome: "write-error", diagnosticId: "timeout-a", recovery: "refresh" });

    expect(pending.forCommand("command-b")).toBe("run-b");
    pending.settle("command-b", { outcome: "write-error", diagnosticId: "timeout-b", recovery: "refresh" });

    expect(pending.forCommand("command-a")).toBe("run-a");
    expect(pending.forCommand("command-b")).toBe("run-b");
  });

  it("releases only the command identity whose outcome is definitive", () => {
    const pending = new PendingCommandIdentitySet(identities("run-a", "run-b", "run-a-next", "run-b-next"));

    expect(pending.forCommand("command-a")).toBe("run-a");
    expect(pending.forCommand("command-b")).toBe("run-b");

    pending.settle("command-a", { outcome: "saved", value: {}, causalityRevision: 1 });
    expect(pending.forCommand("command-a")).toBe("run-a-next");
    expect(pending.forCommand("command-b")).toBe("run-b");

    pending.settle("command-b", { outcome: "stale", diagnosticId: "conflict-b" });
    expect(pending.forCommand("command-b")).toBe("run-b-next");
  });

  it("releases an ambiguous identity once durable truth observes that exact Run", () => {
    const pending = new PendingCommandIdentitySet(identities("run-a", "run-a-next"));
    expect(pending.forCommand("command-a")).toBe("run-a");
    pending.settle("command-a", { outcome: "write-error", diagnosticId: "timeout", recovery: "refresh" });

    pending.observeDurableIdentities(["another-run"]);
    expect(pending.forCommand("command-a")).toBe("run-a");

    pending.observeDurableIdentities(["run-a"]);
    expect(pending.forCommand("command-a")).toBe("run-a-next");
  });

  it("releases non-recoverable write failures", () => {
    const pending = new PendingCommandIdentitySet(identities("run-a", "run-a-next"));
    expect(pending.forCommand("command-a")).toBe("run-a");

    pending.settle("command-a", { outcome: "write-error", diagnosticId: "bad-request", recovery: "blocked" });

    expect(pending.forCommand("command-a")).toBe("run-a-next");
  });
  it("pins command authority while an ambiguous outcome remains unresolved", () => {
    const pending = new PendingCommandIdentitySet(identities("schedule-run-a", "schedule-run-b"));

    const first = pending.bindCommand("schedule-a", { generationId: "generation-1", recordVersion: 3 });
    pending.settle("schedule-a", { outcome: "write-error", diagnosticId: "timeout", recovery: "refresh" });
    const retried = pending.bindCommand("schedule-a", { generationId: "generation-2", recordVersion: 4 });

    expect(retried).toEqual(first);
    expect(retried.identity).toBe("schedule-run-a");
    expect(retried.authority).toEqual({ generationId: "generation-1", recordVersion: 3 });
  });

  it("creates a fresh authority binding after durable settlement", () => {
    const pending = new PendingCommandIdentitySet(identities("schedule-run-a", "schedule-run-b"));

    pending.bindCommand("schedule-a", { generationId: "generation-1", recordVersion: 3 });
    pending.observeDurableIdentities(["schedule-run-a"]);
    const next = pending.bindCommand("schedule-a", { generationId: "generation-2", recordVersion: 4 });

    expect(next).toEqual({
      identity: "schedule-run-b",
      authority: { generationId: "generation-2", recordVersion: 4 },
    });
  });

});
