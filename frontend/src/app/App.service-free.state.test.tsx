import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { FleetSnapshot, LeagueMap } from "@generated/models";
import { i18n } from "@/i18n/setup";
import { saveExperienceMode } from "@/app/preferences";
import { InMemoryApiClient, type DeferredFleetRead } from "@test/InMemoryApiClient";
import { renderServiceFreeApp } from "@test/renderServiceFreeApp";

describe("service-free app state and refresh authority", () => {
  beforeAll(async () => { await i18n.changeLanguage("en"); });
  beforeEach(() => { window.history.replaceState({}, "", "/"); saveExperienceMode("standard"); });

  it("does not publish a failed service-free location write as committed state", async () => {
    const client = new InMemoryApiClient({ league: leagueSnapshot(["/workspace/base"]) });
    client.queueLocationWriteFailure("simulated durable write failure");

    const failed = await client.addLocation("/workspace/new");

    expect(failed.outcome).toBe("write-error");
    expect(await client.locations()).toMatchObject({ locations: ["/workspace/base"], unavailable: [] });
    expect((await client.league()).folders).toEqual(["/workspace/base"]);

    const saved = await client.addLocation("/workspace/new");
    expect(saved.outcome).toBe("saved");
    if (saved.outcome === "saved") expect(saved.causalityRevision).toBe(1);
    expect(await client.locations()).toMatchObject({ locations: ["/workspace/base", "/workspace/new"], unavailable: [] });
    const league = await client.league();
    expect(league.folders).toEqual(["/workspace/base", "/workspace/new"]);
    expect(league.summary.folders).toBe(2);
  });

  it("keeps service-free League and location reads on one committed folder state", async () => {
    const client = new InMemoryApiClient({ league: leagueSnapshot(["/workspace/base"]) });

    await client.addLocation("/workspace/new");
    expect(await client.locations()).toMatchObject({ locations: ["/workspace/base", "/workspace/new"], unavailable: [] });
    expect((await client.league()).folders).toEqual(["/workspace/base", "/workspace/new"]);

    await client.removeLocation("/workspace/base", (await client.locations()).revision);
    expect(await client.locations()).toMatchObject({ locations: ["/workspace/new"], unavailable: [] });
    const league = await client.league();
    expect(league.folders).toEqual(["/workspace/new"]);
    expect(league.summary.folders).toBe(1);
  });

  it("rejects conflicting service-free League and location seed folders", () => {
    expect(() => new InMemoryApiClient({
      locations: ["/workspace/locations"],
      league: leagueSnapshot(["/workspace/league"]),
    })).toThrow("must describe the same committed folders");
  });

  it("isolates service-free Fleet snapshots from seed and read-result mutation", async () => {
    const seed = fleetSnapshot("seed", 4);
    const client = new InMemoryApiClient({ fleet: seed });

    seed.summary.ready_for_update = 99;
    const first = await client.fleet();
    expect(first.summary.ready_for_update).toBe(4);

    first.summary.ready_for_update = 88;
    first.provenance.snapshot_id = "mutated-read";
    const second = await client.fleet();
    expect(second.summary.ready_for_update).toBe(4);
    expect(second.provenance.snapshot_id).toBe("seed");
  });

  it("isolates queued Fleet snapshots from caller mutation", async () => {
    const client = new InMemoryApiClient({ fleet: fleetSnapshot("initial", 1) });
    const queued = fleetSnapshot("queued", 6);
    client.queueFleetRead(queued);

    queued.summary.ready_for_update = 77;
    queued.provenance.snapshot_id = "mutated-before-read";

    const read = await client.fleet();
    expect(read.summary.ready_for_update).toBe(6);
    expect(read.provenance.snapshot_id).toBe("queued");
  });

  it("isolates service-free League, health and privacy reads from caller mutation", async () => {
    const league = leagueSnapshot(["/workspace/base"]);
    league.teams = [{
      repository: { repository_id: "team-a", name: "team-a", path: "/workspace/base" },
      repository_instance_id: "service-free-instance:team-a",
      pipeline: { state: "MISSING", path: "goon.yml" },
    }];
    const client = new InMemoryApiClient({ league });

    const firstLeague = await client.league();
    firstLeague.teams?.splice(0, firstLeague.teams.length);
    expect((await client.league()).teams).toHaveLength(1);

    const firstHealth = await client.health();
    firstHealth.status = "DEGRADED";
    expect((await client.health()).status).toBe("READY");

    const firstPrivacy = await client.privacy();
    firstPrivacy.telemetry = true;
    expect((await client.privacy()).telemetry).toBe(false);
  });

  it("keeps the newest fleet refresh when an older read finishes later", async () => {
    const releaseDiscovery = deferRuntimeDiscovery();
    const fixture = renderServiceFreeApp({ fleet: fleetSnapshot("initial", 2) });
    fireEvent.click(await screen.findByRole("button", { name: "Repositories" }));
    await screen.findByRole("heading", { name: "Repositories" });
    await waitFor(() => expect(readyValue()).toBe("2"));
    await settleRuntimeDiscovery(fixture.client, releaseDiscovery, fleetSnapshot("initial", 2));
    const baselineReads = fixture.client.state.fleetReads;
    fixture.client.setCommittedFleetSnapshot(fleetSnapshot("new", 9));
    const olderRead = fixture.client.deferFleetRead(fleetSnapshot("old", 3));
    const newerRead = fixture.client.deferFleetRead(fleetSnapshot("new", 9));

    const refresh = screen.getByRole("button", { name: "Refresh" });
    await startFleetRefresh(refresh, olderRead);
    await startFleetRefresh(refresh, newerRead);

    expect(await releaseRenderedFleetRead(newerRead)).toBe("released");
    await waitFor(() => expect(readyValue()).toBe("9"));
    expect(await olderRead.settled).toBe("aborted");
    expect(await releaseRenderedFleetRead(olderRead)).toBe("aborted");
    expect(readyValue()).toBe("9");

    const followUpRead = fixture.client.deferFleetRead(fleetSnapshot("new-follow-up", 9));
    await startFleetRefresh(refresh, followUpRead);
    expect(screen.getByText("Refreshing the latest data…")).toBeTruthy();
    expect(await releaseRenderedFleetRead(followUpRead)).toBe("released");
    await waitFor(() => expect(screen.queryByText("Refreshing the latest data…")).toBeNull());
    expect(fixture.client.state.fleetReads).toBeGreaterThan(baselineReads + 2);
    expect(readyValue()).toBe("9");
  });

  it("keeps queued Fleet reads transient instead of rewriting committed fixture state", async () => {
    const client = new InMemoryApiClient({ fleet: fleetSnapshot("initial", 2) });
    client.setCommittedFleetSnapshot(fleetSnapshot("committed", 9));
    const olderGate = client.deferFleetRead(fleetSnapshot("old", 3));
    const newerGate = client.deferFleetRead(fleetSnapshot("new", 9));

    const oldRead = client.fleet();
    const newRead = client.fleet();
    await Promise.all([olderGate.started, newerGate.started]);

    newerGate.release();
    expect((await newRead).provenance.snapshot_id).toBe("new");
    olderGate.release();
    expect((await oldRead).provenance.snapshot_id).toBe("old");
    const nextRead = await client.fleet();
    expect(nextRead.provenance.snapshot_id).toBe("committed");
    expect(nextRead.summary.ready_for_update).toBe(9);
  });

  it("keeps previously confirmed fleet data usable when refresh fails", async () => {
    const releaseDiscovery = deferRuntimeDiscovery();
    const fixture = renderServiceFreeApp({ fleet: fleetSnapshot("stable", 4) });
    fireEvent.click(await screen.findByRole("button", { name: "Repositories" }));
    await screen.findByRole("heading", { name: "Repositories" });
    await waitFor(() => expect(readyValue()).toBe("4"));
    await settleRuntimeDiscovery(fixture.client, releaseDiscovery, fleetSnapshot("stable", 4));
    const failedRead = fixture.client.deferFleetFailure("temporary fixture failure");

    await startFleetRefresh(screen.getByRole("button", { name: "Refresh" }), failedRead);
    expect(await releaseRenderedFleetRead(failedRead)).toBe("released");

    expect(await screen.findByText("Showing previously loaded data")).toBeTruthy();
    expect(readyValue()).toBe("4");
  });

  it("supersedes an operator refresh when initial runtime discovery arrives later", async () => {
    const releaseDiscovery = deferRuntimeDiscovery();
    const fixture = renderServiceFreeApp({ fleet: fleetSnapshot("stable", 4) });
    fireEvent.click(await screen.findByRole("button", { name: "Repositories" }));
    await screen.findByRole("heading", { name: "Repositories" });
    await waitFor(() => expect(readyValue()).toBe("4"));
    const failedRead = fixture.client.deferFleetFailure("superseded fixture failure");
    await startFleetRefresh(screen.getByRole("button", { name: "Refresh" }), failedRead);

    const discoveredRead = fixture.client.deferFleetRead(fleetSnapshot("discovered", 7));
    await act(async () => { releaseDiscovery(); });
    await discoveredRead.started;
    expect(await failedRead.settled).toBe("aborted");
    expect(await releaseRenderedFleetRead(failedRead)).toBe("aborted");
    expect(await releaseRenderedFleetRead(discoveredRead)).toBe("released");
    await waitFor(() => expect(readyValue()).toBe("7"));
    expect(screen.queryByText("Showing previously loaded data")).toBeNull();
  });
});



function deferRuntimeDiscovery(): () => void {
  let release!: () => void;
  const discovery = new Promise<void>((resolve) => { release = resolve; });
  const overview = InMemoryApiClient.prototype.fleetRunOverview;
  vi.spyOn(InMemoryApiClient.prototype, "fleetRunOverview").mockImplementation(async function (this: InMemoryApiClient) {
    await discovery;
    return overview.call(this);
  });
  return release;
}

async function settleRuntimeDiscovery(client: InMemoryApiClient, release: () => void, snapshot: FleetSnapshot): Promise<void> {
  // Seeded Fleet rendering precedes runtime discovery. Consume its one initial
  // invalidation explicitly before an independent operator-refresh scenario.
  const initialRefresh = client.deferFleetRead(snapshot);
  await act(async () => { release(); });
  await initialRefresh.started;
  expect(await releaseRenderedFleetRead(initialRefresh)).toBe("released");
}

async function startFleetRefresh(refresh: HTMLElement, read: DeferredFleetRead): Promise<void> {
  await act(async () => {
    fireEvent.click(refresh);
    await read.started;
  });
}

async function releaseRenderedFleetRead(read: DeferredFleetRead): Promise<"released" | "aborted"> {
  let outcome: "released" | "aborted" | undefined;
  await act(async () => {
    read.release();
    outcome = await read.settled;
  });
  if (outcome === undefined) throw new Error("Deferred Fleet read did not settle");
  return outcome;
}

function fleetSnapshot(id: string, ready: number): FleetSnapshot {
  return {
    attention: { action_required: 0, groups: [], healthy: ready },
    provenance: {
      generated_at: `2026-08-10T12:00:0${ready}Z`,
      journal_revision: ready + 1,
      snapshot_id: id,
      durable_revision: ready + 1,
    },
    repositories: [],
    review_preparation: null,
    summary: { discovered: ready, excluded: 0, included: ready, needs_attention: 0, ready_for_update: ready },
  };
}

function readyValue(): string | null {
  const label = screen.getByText("Ready to update");
  const article = label.closest("article");
  if (article === null) throw new Error("Ready summary card is missing");
  return article.querySelector("strong")?.textContent ?? null;
}
function leagueSnapshot(folders: string[]): LeagueMap {
  return {
    folders,
    teams: [],
    summary: {
      folders: folders.length,
      git_repositories: 0,
      configured_teams: 0,
      missing_goon_yml: 0,
      invalid_goon_yml: 0,
    },
  };
}
