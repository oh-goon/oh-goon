import { describe, expect, it } from "vitest";

import { ReadController } from "@/domain/concurrency/ReadController";

describe("ReadController", () => {
  it("accepts only the newest result for the same read scope", async () => {
    const controller = new ReadController();
    let finishOld!: (value: string) => void;
    const oldRead = controller.execute("history-detail", "run-a", () => new Promise<string>((resolve) => { finishOld = resolve; }));
    const newRead = controller.execute("history-detail", "run-b", async () => "run-b-detail");

    await expect(newRead).resolves.toEqual({ outcome: "accepted", value: "run-b-detail", causalityRevision: 0 });
    finishOld("run-a-detail");
    await expect(oldRead).resolves.toEqual({ outcome: "superseded" });
  });

  it("rejects an old completion after an A to B to A identity cycle", async () => {
    const controller = new ReadController();
    let finishOldA!: (value: string) => void;
    const oldA = controller.execute("history-detail", "run-a", () => new Promise<string>((resolve) => { finishOldA = resolve; }));

    await expect(controller.execute("history-detail", "run-b", async () => "run-b-detail")).resolves.toEqual({
      outcome: "accepted",
      value: "run-b-detail",
      causalityRevision: 0,
    });
    await expect(controller.execute("history-detail", "run-a", async () => "new-run-a-detail")).resolves.toEqual({
      outcome: "accepted",
      value: "new-run-a-detail",
      causalityRevision: 0,
    });

    finishOldA("old-run-a-detail");
    await expect(oldA).resolves.toEqual({ outcome: "superseded" });
  });

  it("aborts transport work when a read scope is invalidated", async () => {
    const controller = new ReadController();
    let observedAbort = false;
    const pending = controller.execute("folder-browser", "folder-a", (signal) => new Promise<string>((resolve) => {
      signal.addEventListener("abort", () => { observedAbort = true; resolve("late"); }, { once: true });
    }));

    controller.invalidate("folder-browser");
    await expect(pending).resolves.toEqual({ outcome: "superseded" });
    expect(observedAbort).toBe(true);
  });

  it("rejects a canceled result even when transport ignores AbortSignal", async () => {
    const controller = new ReadController();
    let finish!: (value: string) => void;
    const pending = controller.execute("fleet", null, () => new Promise<string>((resolve) => { finish = resolve; }));

    controller.cancel("fleet");
    finish("late fleet");

    await expect(pending).resolves.toEqual({ outcome: "superseded" });
  });

  it("projects owner state only for the accepted read generation", async () => {
    const controller = new ReadController();
    const projected: string[] = [];
    let finishOld!: (value: string) => void;
    let finishCurrent!: (value: string) => void;
    const oldRead = controller.execute(
      "game-tape-live-output",
      "run-a",
      () => new Promise<string>((resolve) => { finishOld = resolve; }),
      (value) => {
        projected.push(value);
        return `accepted:${value}`;
      },
    );
    const currentRead = controller.execute(
      "game-tape-live-output",
      "run-a",
      () => new Promise<string>((resolve) => { finishCurrent = resolve; }),
      (value) => {
        projected.push(value);
        return `accepted:${value}`;
      },
    );

    finishCurrent("current");
    await expect(currentRead).resolves.toEqual({
      outcome: "accepted",
      value: "accepted:current",
      causalityRevision: 0,
    });
    expect(projected).toEqual(["current"]);

    finishOld("stale");
    await expect(oldRead).resolves.toEqual({ outcome: "superseded" });
    expect(projected).toEqual(["current"]);
  });

  it("allows independent read scopes to progress concurrently", async () => {
    const controller = new ReadController();
    const fleet = controller.execute("fleet", null, async () => "fleet");
    const health = controller.execute("health", null, async () => "health");
    await expect(Promise.all([fleet, health])).resolves.toEqual([
      { outcome: "accepted", value: "fleet", causalityRevision: 0 },
      { outcome: "accepted", value: "health", causalityRevision: 0 },
    ]);
  });

  it("rejects a pre-write read after causal invalidation", async () => {
    const controller = new ReadController();
    let finish!: (value: string) => void;
    const oldRead = controller.execute("fleet", null, () => new Promise<string>((resolve) => { finish = resolve; }));

    controller.causality.observe(4);
    controller.invalidate("fleet", 4);
    finish("old fleet");
    await expect(oldRead).resolves.toEqual({ outcome: "superseded" });

    await expect(controller.execute("fleet", null, async () => "new fleet")).resolves.toEqual({
      outcome: "accepted",
      value: "new fleet",
      causalityRevision: 4,
    });
  });

  it("fails closed when two mounted consumers claim the same read scope", () => {
    const controller = new ReadController();
    const first = Symbol("first");
    const second = Symbol("second");

    controller.acquireOwner("shared-plan-document", first);
    expect(() => controller.acquireOwner("shared-plan-document", second)).toThrow(
      "Read scope already has a mounted owner: shared-plan-document",
    );

    controller.releaseOwner("shared-plan-document", first);
    expect(() => controller.acquireOwner("shared-plan-document", second)).not.toThrow();
  });

  it("does not let a stale owner release the current read-scope lease", () => {
    const controller = new ReadController();
    const first = Symbol("first");
    const second = Symbol("second");

    controller.acquireOwner("fleet", first);
    controller.releaseOwner("fleet", second);
    expect(() => controller.acquireOwner("fleet", second)).toThrow(
      "Read scope already has a mounted owner: fleet",
    );
  });
});
