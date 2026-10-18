import { render, type RenderResult } from "@testing-library/react";

import { ApiClientProvider } from "@/app/ApiClientProvider";
import { CausalityClock } from "@/domain/concurrency/CausalityClock";
import { MutationController } from "@/domain/concurrency/MutationController";
import { ReadController } from "@/domain/concurrency/ReadController";
import { App } from "@/app/App";
import { AppBootstrapProvider } from "@/app/bootstrap/AppBootstrapProvider";
import { FleetRunProvider } from "@/app/FleetRunProvider";
import { InMemoryApiClient, type InMemoryApiSeed } from "@test/InMemoryApiClient";

export interface ServiceFreeAppFixture {
  client: InMemoryApiClient;
  view: RenderResult;
}

export function renderServiceFreeApp(seed: InMemoryApiSeed = {}): ServiceFreeAppFixture {
  const causality = new CausalityClock();
  const mutations = new MutationController(causality);
  const reads = new ReadController(causality);
  const client = new InMemoryApiClient(seed, mutations);
  const view = render(
    <ApiClientProvider client={client} mutationController={mutations} readController={reads}>
      <AppBootstrapProvider>
        <FleetRunProvider>
          <App />
        </FleetRunProvider>
      </AppBootstrapProvider>
    </ApiClientProvider>,
  );
  return { client, view };
}
