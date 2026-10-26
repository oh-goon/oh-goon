import { describe, expect, it } from "vitest";

import { CausalityClock } from "@/domain/concurrency/CausalityClock";
import { MutationBusyError, MutationController } from "@/domain/concurrency/MutationController";
import { ReadController } from "@/domain/concurrency/ReadController";
import { readScopesInvalidatedByMutation } from "@/domain/reads/readResources";

describe("MutationController", () => {
  it("admits only one synchronous write at a time", async () => {
    const controller = new MutationController();
    let release!: () => void;
    const pending = controller.execute("locations", () => new Promise<void>((resolve) => { release = resolve; }));

    await expect(controller.execute("repositories", async () => undefined)).rejects.toBeInstanceOf(MutationBusyError);
    release();
    await expect(pending).resolves.toEqual({ value: undefined, causalityRevision: 1 });
    expect(controller.getSnapshot()).toBe(false);
  });

  it("always accepts a successful dispatched write", async () => {
    const controller = new MutationController();
    let finish!: (value: string) => void;
    const pending = controller.execute("schedules", () => new Promise<string>((resolve) => { finish = resolve; }));

    finish("saved response");

    await expect(pending).resolves.toEqual({ value: "saved response", causalityRevision: 1 });
    expect(controller.getSnapshot()).toBe(false);
  });

  it("advances causality for every successful write", async () => {
    const controller = new MutationController();
    await controller.execute("locations", async () => "first");
    await controller.execute("schedules", async () => "second");
    expect(controller.causality.current()).toBe(2);
  });

  it("publishes one successful write revision through the shared read/write clock", async () => {
    const causality = new CausalityClock();
    const mutations = new MutationController(causality);
    const reads = new ReadController(causality);

    await mutations.execute("locations", async () => "saved");

    await expect(reads.execute("locations", null, async () => "fresh")).resolves.toEqual({
      outcome: "accepted",
      value: "fresh",
      causalityRevision: 1,
    });
  });

  it("publishes durable commits independently of feature feedback", async () => {
    const controller = new MutationController();
    const commits: Array<{ scope: string; revision: number }> = [];
    controller.subscribeCommits((scope, revision) => commits.push({ scope, revision }));

    await controller.execute("repositories", async () => "saved");

    expect(commits).toEqual([{ scope: "repositories", revision: 1 }]);
  });

  it("supersedes dependent reads as soon as the write boundary commits", async () => {
    const causality = new CausalityClock();
    const mutations = new MutationController(causality);
    const reads = new ReadController(causality);
    mutations.subscribeCommits((scope, revision) => {
      reads.invalidateMany(readScopesInvalidatedByMutation(scope), revision);
    });

    let finishRead!: (value: string) => void;
    const staleRead = reads.execute("fleet", null, () => new Promise<string>((resolve) => { finishRead = resolve; }));

    await mutations.execute("repositories", async () => "saved");
    finishRead("old fleet snapshot");

    await expect(staleRead).resolves.toEqual({ outcome: "superseded" });
  });

  it("does not let commit-observer failure rewrite a saved server mutation as a write failure", async () => {
    const controller = new MutationController();
    controller.subscribeCommits(() => { throw new Error("presentation invalidation failed"); });

    await expect(controller.execute("schedules", async () => "saved response")).resolves.toEqual({
      value: "saved response",
      causalityRevision: 1,
    });
  });

  it("keeps cancellation exclusive during an ordinary write", async () => {
    const controller = new MutationController();
    let finish!: () => void;
    const pending = controller.execute("fleet-run", () => new Promise<void>((resolve) => { finish = resolve; }));
    const cancel = async () => "cancelled";

    await expect(controller.execute("fleet-run", cancel, "fleet-run-cancel")).rejects.toBeInstanceOf(MutationBusyError);
    await expect(controller.execute("fleet-run", async () => undefined, "agent-repair")).rejects.toBeInstanceOf(MutationBusyError);
    finish();
    await pending;
    await expect(controller.execute("fleet-run", cancel, "fleet-run-cancel")).resolves.toEqual({ value: "cancelled", causalityRevision: 2 });
    expect(controller.getSnapshot()).toBe(false);
  });

  it("releases a failed cancellation for retry without releasing the repair write fence", async () => {
    const controller = new MutationController();
    let finishRepair!: () => void;
    let failCancellation!: (error: Error) => void;
    const repair = controller.execute("fleet-run", () => new Promise<void>((resolve) => { finishRepair = resolve; }), "agent-repair");
    const cancellation = controller.execute("fleet-run", () => new Promise<void>((_resolve, reject) => { failCancellation = reject; }), "fleet-run-cancel");

    await expect(controller.execute("fleet-run", async () => undefined, "fleet-run-cancel")).rejects.toBeInstanceOf(MutationBusyError);
    failCancellation(new Error("cancellation transport failed"));
    await expect(cancellation).rejects.toThrow("cancellation transport failed");
    expect(controller.getSnapshot()).toBe(true);
    expect(controller.causality.current()).toBe(0);
    await expect(controller.execute("locations", async () => undefined)).rejects.toBeInstanceOf(MutationBusyError);
    await expect(controller.execute("fleet-run", async () => "cancelled", "fleet-run-cancel")).resolves.toEqual({ value: "cancelled", causalityRevision: 1 });
    expect(controller.getSnapshot()).toBe(true);
    finishRepair();
    await repair;
    expect(controller.causality.current()).toBe(2);
    expect(controller.getSnapshot()).toBe(false);
  });

  it("retains a dispatched cancellation's write fence when repair transport fails first", async () => {
    const controller = new MutationController();
    let failRepair!: (error: Error) => void;
    let finishCancellation!: () => void;
    const repair = controller.execute("fleet-run", () => new Promise<void>((_resolve, reject) => { failRepair = reject; }), "agent-repair");
    const cancellation = controller.execute("fleet-run", () => new Promise<void>((resolve) => { finishCancellation = resolve; }), "fleet-run-cancel");

    failRepair(new Error("repair connection lost"));
    await expect(repair).rejects.toThrow("repair connection lost");
    expect(controller.getSnapshot()).toBe(true);
    await expect(controller.execute("fleet-run", async () => undefined, "agent-repair")).rejects.toBeInstanceOf(MutationBusyError);
    await expect(controller.execute("fleet-run", async () => undefined, "fleet-run-cancel")).rejects.toBeInstanceOf(MutationBusyError);
    finishCancellation();
    await expect(cancellation).resolves.toEqual({ value: undefined, causalityRevision: 1 });
    expect(controller.getSnapshot()).toBe(false);
  });
});
