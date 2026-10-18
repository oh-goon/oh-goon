import { act, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiClientProvider, useApiClient } from "@/app/ApiClientProvider";
import { CausalityClock } from "@/domain/concurrency/CausalityClock";
import { MutationController } from "@/domain/concurrency/MutationController";
import { ReadController } from "@/domain/concurrency/ReadController";
import { InMemoryApiClient } from "@test/InMemoryApiClient";

function Probe({ expected }: { expected: InMemoryApiClient }) {
  expect(useApiClient()).toBe(expected);
  return <span>ready</span>;
}

describe("ApiClientProvider transport authority", () => {
  it("keeps client and controller identities mount-bound", () => {
    const causality = new CausalityClock();
    const mutations = new MutationController(causality);
    const reads = new ReadController(causality);
    const client = new InMemoryApiClient({}, mutations);
    const view = render(
      <ApiClientProvider
        client={client}
        mutationController={mutations}
        readController={reads}
      >
        <Probe expected={client} />
      </ApiClientProvider>,
    );

    view.rerender(
      <ApiClientProvider
        client={client}
        mutationController={mutations}
        readController={reads}
      >
        <Probe expected={client} />
      </ApiClientProvider>,
    );

    const replacement = new MutationController(causality);
    expect(() => view.rerender(
      <ApiClientProvider
        client={client}
        mutationController={replacement}
        readController={reads}
      >
        <Probe expected={client} />
      </ApiClientProvider>,
    )).toThrow(/identities are mount-bound/);
  });
  it("invalidates dependent reads on commits and conflicts without inventing conflict causality", async () => {
    const causality = new CausalityClock();
    const mutations = new MutationController(causality);
    const reads = new ReadController(causality);
    const invalidateMany = vi.spyOn(reads, "invalidateMany");
    const client = new InMemoryApiClient({}, mutations);
    render(
      <ApiClientProvider
        client={client}
        mutationController={mutations}
        readController={reads}
      >
        <Probe expected={client} />
      </ApiClientProvider>,
    );

    await act(async () => {
      await mutations.execute("schedules", async () => undefined);
    });
    expect(causality.current()).toBe(1);
    expect(invalidateMany).toHaveBeenLastCalledWith(expect.any(Array), 1);

    act(() => mutations.reportConflict("schedules"));
    expect(causality.current()).toBe(1);
    expect(invalidateMany).toHaveBeenLastCalledWith(expect.any(Array));
  });

});
