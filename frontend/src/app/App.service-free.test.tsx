import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { i18n } from "@/i18n/setup";
import { saveExperienceMode } from "@/app/preferences";
import { renderServiceFreeApp } from "@test/renderServiceFreeApp";

describe("service-free app fixture", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("en");
  });

  beforeEach(() => {
    window.history.replaceState({}, "", "/");
    saveExperienceMode("standard");
  });

  it("runs navigation, edit and save without a backend", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    renderServiceFreeApp({ locations: ["/workspace/base"], browsePath: "/workspace/new" });

    await screen.findByRole("heading", { name: "Overview" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    await screen.findByRole("heading", { name: "Settings" }, { timeout: 5_000 });
    fireEvent.click(screen.getByRole("button", { name: "Add repository location" }));
    fireEvent.click(await screen.findByRole("button", { name: "Use this folder" }));

    expect(await screen.findByText("/workspace/new")).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("labels repository-location removal as stopping discovery rather than deleting a folder", async () => {
    const fixture = renderServiceFreeApp({ locations: ["/workspace/base"] });
    fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
    await screen.findByRole("heading", { name: "Settings" }, { timeout: 5_000 });

    screen.getByText("/workspace/base");
    expect(screen.getByText(/never deletes files on disk/i)).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Stop using" }));

    // The default Testing Library async timeout is one second. Under the full
    // frontend gate, unrelated concurrent fixture work can delay React's
    // invalidation-driven repaint beyond that without changing mutation truth.
    // Prove the durable service-free mutation first, then give the presentation
    // cut a bounded window to converge instead of treating 1s scheduler latency
    // as a repository-location semantic failure.
    await waitFor(async () => {
      expect((await fixture.client.locations()).locations).toEqual([]);
    }, { timeout: 5_000 });
    await waitFor(() => expect(screen.queryByText("/workspace/base")).toBeNull(), { timeout: 5_000 });
  });

  it("closes expanded technical diagnostics when the user switches back to Standard mode", async () => {
    saveExperienceMode("advanced");
    renderServiceFreeApp({ locations: ["/workspace/base"] });
    fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
    await screen.findByRole("heading", { name: "Settings" }, { timeout: 5_000 });
    const advancedToggle = screen.getByRole("button", { name: /Technical settings and diagnostics/ });
    expect(advancedToggle.getAttribute("aria-expanded")).toBe("true");
    expect(advancedToggle.getAttribute("aria-controls")).toBe("advanced-settings-content");
    expect(document.getElementById("advanced-settings-content")).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Standard" }));

    await waitFor(() => expect(advancedToggle.getAttribute("aria-expanded")).toBe("false"));
    expect(document.getElementById("advanced-settings-content")).toBeNull();
  });

  it("keeps the standard shell focused on everyday product navigation", async () => {
    renderServiceFreeApp({ locations: ["/workspace/base"] });
    await screen.findByRole("heading", { name: "Overview" });

    expect(screen.getByRole("button", { name: "Overview" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Repositories" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Dependencies" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Plans" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Runs" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Schedules" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "New run" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Tools" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Recovery" })).toBeNull();
    expect(screen.getByRole("button", { name: "Settings" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Help" })).toBeTruthy();
  });


  it("shows each primary destination only once in Advanced mode", async () => {
    saveExperienceMode("advanced");
    renderServiceFreeApp({ locations: ["/workspace/base"] });
    await screen.findByRole("heading", { name: "Overview" });

    expect(screen.getAllByRole("button", { name: "Dependencies" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Recovery" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Tools" })).toHaveLength(1);
  });


  it("keeps drill-down pages anchored to their parent navigation destination", async () => {
    window.history.replaceState({}, "", "/#shared-runs");
    renderServiceFreeApp({ locations: ["/workspace/base"] });

    await screen.findByRole("heading", { name: "New shared run" });
    expect(screen.getByRole("button", { name: "Runs" }).getAttribute("aria-current")).toBe("page");
  });

  it("shows configured folders that are unavailable without calling them execution failures", async () => {
    renderServiceFreeApp({ locations: ["/workspace/missing"], unavailableLocations: ["/workspace/missing"] });
    fireEvent.click(await screen.findByRole("button", { name: "Repositories" }));
    await screen.findByRole("heading", { name: "Repositories" });
    expect(await screen.findByText("A configured repository location is unavailable")).toBeTruthy();
    expect(screen.getByText("/workspace/missing")).toBeTruthy();
  });

});
