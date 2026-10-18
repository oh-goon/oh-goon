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

});
