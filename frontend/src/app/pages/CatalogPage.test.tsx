import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClientProvider } from "@/app/ApiClientProvider";
import { AppBootstrapProvider } from "@/app/bootstrap/AppBootstrapProvider";
import { FleetRunProvider } from "@/app/FleetRunProvider";
import { CatalogPage } from "@/app/pages/CatalogPage";
import { i18n } from "@/i18n/setup";
import { InMemoryApiClient } from "@test/InMemoryApiClient";
import { FleetRunStatus, GameTapeStatus } from "@generated/enums";
import type { CatalogExploration, FleetRunState, RepositoryIdentity } from "@generated/models";
import { createIncludedFleet } from "../../../e2e/BrowserScaleFixture";
import { CATALOG_FIXTURE_ARTIFACT_SHA, CATALOG_FIXTURE_CANDIDATE, CATALOG_FIXTURE_REFERENCE, CATALOG_FIXTURE_TARGET, catalogFixtureExploration, catalogFixturePreview, catalogFixtureRunDetail } from "../../../e2e/fixtures/catalog";
import { FIXTURE_REPOSITORIES } from "../../../e2e/fixtures/identities";
import { runDetail, runState } from "../../../e2e/fixtures/runProjection";

const primary = FIXTURE_REPOSITORIES.catalog;
const secondary = FIXTURE_REPOSITORIES.web;

async function fixture() {
  const fleet = createIncludedFleet([primary, secondary], "2026-11-06T12:00:00Z");
  const client = new InMemoryApiClient({ fleet });
  const explore = vi.spyOn(client, "catalogExplore").mockImplementation(async (repositoryId) => catalogFixtureExploration(repositoryId === primary.repository_id ? primary : secondary));
  const preview = vi.spyOn(client, "catalogPreview").mockImplementation(async (repositoryId) => catalogFixturePreview(repositoryId === primary.repository_id ? primary : secondary));
  const start = vi.spyOn(client, "startCatalogRun");
  const exportArtifact = vi.spyOn(client, "exportRunArtifact");
  const openRun = vi.fn();
  render(<ApiClientProvider client={client}><AppBootstrapProvider><FleetRunProvider>
    <CatalogPage onOpenRun={openRun} onOpenRepositories={vi.fn()} />
  </FleetRunProvider></AppBootstrapProvider></ApiClientProvider>);
  await screen.findByRole("combobox", { name: "Repository" });
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Repository" })).toBeEnabled());
  return { client, explore, preview, start, exportArtifact, openRun };
}

async function exploreRepository(repository: RepositoryIdentity = primary) {
  fireEvent.change(screen.getByRole("combobox", { name: "Repository" }), { target: { value: repository.repository_id } });
  fireEvent.change(screen.getByRole("textbox", { name: "Search code" }), { target: { value: "submit_order" } });
  await waitFor(() => expect(screen.getByRole("button", { name: "Explore repository" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Explore repository" }));
  await screen.findByRole("heading", { name: "Hashmarks evidence" });
}

function fillCandidate() {
  for (const [label, value] of [
    ["Component name", CATALOG_FIXTURE_CANDIDATE.name],
    ["Description", CATALOG_FIXTURE_CANDIDATE.description],
    ["Owner reference", CATALOG_FIXTURE_CANDIDATE.owner],
    ["Languages (comma separated)", CATALOG_FIXTURE_CANDIDATE.languages.join(", ")],
  ]) fireEvent.change(screen.getByRole("textbox", { name: label }), { target: { value } });
  fireEvent.change(screen.getByRole("combobox", { name: "Component type" }), { target: { value: CATALOG_FIXTURE_CANDIDATE.type } });
  fireEvent.change(screen.getByRole("combobox", { name: "Lifecycle" }), { target: { value: CATALOG_FIXTURE_CANDIDATE.lifecycle } });
}

async function approveCandidate() {
  await exploreRepository();
  fillCandidate();
  fireEvent.click(screen.getByRole("button", { name: "Preview catalog" }));
  const approval = await screen.findByRole("checkbox", { name: "I approve every displayed value for this observed source." });
  fireEvent.click(approval);
}

function completeRun(client: InMemoryApiClient, status: FleetRunState["status"] = FleetRunStatus.COMPLETED, commandFailed = false) {
  let recorded: ReturnType<typeof runDetail> | undefined;
  vi.spyOn(client, "fleetRunDetail").mockImplementation(async (runId) => {
    if (!recorded || recorded.run.fleet_run_id !== runId) throw new Error("Unrecorded Run selected");
    return recorded;
  });
  return vi.spyOn(client, "startCatalogRun").mockImplementation(async (repositoryId, request) => {
    const repository = repositoryId === primary.repository_id ? primary : secondary;
    const run = runState(request.fleet_run_id, status, [repository], 1, 1, CATALOG_FIXTURE_TARGET);
    recorded = catalogFixtureRunDetail(runDetail(run, commandFailed ? GameTapeStatus.ATTENTION_REQUIRED : status === FleetRunStatus.COMPLETED ? GameTapeStatus.PASSED : status === FleetRunStatus.CANCELLED ? GameTapeStatus.CANCELLED : GameTapeStatus.FAILED, status === FleetRunStatus.FAILED || commandFailed));
    if (commandFailed) {
      run.progress = { total: 1, completed: 1, succeeded: 0, failed: 1, needs_attention: 1 };
      recorded.game_tape.status = GameTapeStatus.ATTENTION_REQUIRED;
      recorded.game_tape.summary = { ...recorded.game_tape.summary, passed: 0, failed: 1, attention_required: 1, passed_steps: 0, failed_steps: 1 };
      const lane = recorded.game_tape.repositories[0];
      const step = lane?.steps?.[0];
      if (!lane || !step) throw new Error("Missing recorded failed command");
      lane.status = GameTapeStatus.FAILED;
      step.status = GameTapeStatus.FAILED;
      step.exit_code = 1;
      step.settlement = { owner: "fleet_step_settlement", outcome: "FAILED", cleanup: "VERIFIED", reason: "FAILED_EXIT", observed_returncode: 1 };
    }
    return { outcome: "saved", causalityRevision: 1, value: run };
  });
}

describe("guided catalog review", () => {
  beforeEach(async () => { window.history.replaceState({}, "", "#catalog"); await i18n.changeLanguage("en"); });

  it("requires explicit discovery and revokes approval whenever candidate values change", async () => {
    const { explore, preview, start } = await fixture();
    expect(explore).not.toHaveBeenCalled();
    await approveCandidate();
    expect(preview).toHaveBeenCalledWith(primary.repository_id, { source_authority: catalogFixtureExploration(primary).source_authority, candidate: CATALOG_FIXTURE_CANDIDATE }, expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(screen.getByRole("button", { name: "Generate catalog" })).toBeEnabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Description" }), { target: { value: "Changed after approval" } });
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Generate catalog" })).toBeNull();
    expect(start).not.toHaveBeenCalled();
    expect(screen.getByText("unknown", { exact: true })).toBeTruthy();
    expect(screen.getByText("Observation completeness").nextElementSibling).toHaveTextContent(/^complete$/);
    expect(screen.getByText("Repository-wide coverage").nextElementSibling).toHaveTextContent(/^not-claimed$/);
    expect(screen.getByText(/A commit author is not the catalog owner/)).toBeTruthy();
  });

  it("cannot deliver an old repository response into a new selection", async () => {
    const { explore } = await fixture();
    let release: ((value: CatalogExploration) => void) | undefined;
    explore.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
    fireEvent.change(screen.getByRole("combobox", { name: "Repository" }), { target: { value: primary.repository_id } });
    fireEvent.change(screen.getByRole("textbox", { name: "Search code" }), { target: { value: "submit_order" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Explore repository" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Explore repository" }));
    await waitFor(() => expect(explore).toHaveBeenCalledOnce());
    await exploreRepository(secondary);
    await act(async () => { release?.(catalogFixtureExploration(primary)); });
    expect(screen.getByText("Demo Web Contributor")).toBeTruthy();
    expect(screen.queryByText("Demo Catalog Contributor")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("refuses structurally valid evidence for the wrong repository", async () => {
    const { explore } = await fixture();
    explore.mockResolvedValue(catalogFixtureExploration(secondary));
    fireEvent.change(screen.getByRole("combobox", { name: "Repository" }), { target: { value: primary.repository_id } });
    fireEvent.change(screen.getByRole("textbox", { name: "Search code" }), { target: { value: "submit_order" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Explore repository" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Explore repository" }));
    await screen.findByRole("alert");
    expect(screen.queryByRole("heading", { name: "Review every exported value" })).toBeNull();
  });

  it("keeps empty, incomplete and unsupported observations visible without inventing an owner", async () => {
    const { explore } = await fixture();
    const observation = catalogFixtureExploration(primary);
    explore.mockResolvedValue({ ...observation, hits: [], relations: [], relationship_status: "unsupported", relationship_message: "No supported relationships were returned.", languages: [], qualification: { ...observation.qualification, completeness: "incomplete", truncated: true, negative_evidence: "not-admissible" }, git_author: { status: "unavailable", message: "Native Git history unavailable" } });
    await exploreRepository();
    expect(screen.getByText(/No matching source was returned/)).toBeTruthy();
    expect(screen.getByText(/No supported relationships were returned/)).toBeTruthy();
    expect(screen.getByText("Native Git history unavailable")).toBeTruthy();
    expect(screen.getByText("Truncated", { exact: true })).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Owner reference" })).toHaveValue("");
    expect(screen.queryByRole("button", { name: "Generate catalog" })).toBeNull();
  });

  it("shows file-level evidence without fabricating a symbol or source line", async () => {
    const { explore } = await fixture();
    const observation = catalogFixtureExploration(primary);
    explore.mockResolvedValue({ ...observation, hits: [{ path: "README.md", kind: "file", name: null, qualname: null, signature: null, first_line: null, last_line: null }] });
    await exploreRepository();
    expect(screen.getByText("README.md · File-level evidence; line unavailable")).toBeTruthy();
    expect(screen.queryByText(/README.md:0/)).toBeNull();
  });

  it("keeps search limits distinct from native relationship and lifecycle cuts", async () => {
    const { explore } = await fixture();
    explore.mockResolvedValue({ ...catalogFixtureExploration(primary), relationship_qualification: { max_depth: 1, call_limit_per_symbol: 8, ref_limit_per_symbol: 8, unresolved_reasons: ["call-limit-reached"], external_or_unindexed_reasons: ["external-target-unindexed"], lifecycle_completeness: "incomplete", lifecycle_truncated: true } });
    await exploreRepository();
    expect(screen.getByText("Search result limits").nextElementSibling).toHaveTextContent(/^Not truncated$/);
    expect(screen.getByText("Calls per symbol limit").nextElementSibling).toHaveTextContent(/^8$/);
    expect(screen.getByText("Lifecycle observation completeness").nextElementSibling).toHaveTextContent(/^incomplete$/);
    expect(screen.getByText("Lifecycle observation limits").nextElementSibling).toHaveTextContent(/^Truncated$/);
    expect(screen.getByText("call-limit-reached")).toBeTruthy();
    expect(screen.getByText("external-target-unindexed")).toBeTruthy();
  });

  it("refuses a preview returned for a different observed source", async () => {
    const { preview } = await fixture();
    preview.mockResolvedValue({ ...catalogFixturePreview(primary), source_authority: catalogFixtureExploration(secondary).source_authority });
    await exploreRepository();
    fillCandidate();
    fireEvent.click(screen.getByRole("button", { name: "Preview catalog" }));
    await screen.findByRole("alert");
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Generate catalog" })).toBeNull();
  });

  it("generates from exact approved authority and exports only the original recorded artifact", async () => {
    const setup = await fixture();
    const start = completeRun(setup.client);
    await approveCandidate();
    fireEvent.click(screen.getByRole("button", { name: "Generate catalog" }));
    await screen.findByRole("heading", { name: "Export the original retained artifact" });
    const submitted = start.mock.calls[0]?.[1];
    expect(submitted).toMatchObject({ source_authority: catalogFixtureExploration(primary).source_authority, candidate: CATALOG_FIXTURE_CANDIDATE, expected_plan_id: catalogFixturePreview(primary).plan_id, expected_plan_sha256: catalogFixturePreview(primary).plan_sha256, expected_execution_digest: catalogFixturePreview(primary).execution_digest });
    if (!submitted) throw new Error("Missing original submission");
    await waitFor(() => expect(screen.getByRole("button", { name: "Export retained artifact" })).toBeDisabled());
    fireEvent.change(screen.getByRole("textbox", { name: "Export destination" }), { target: { value: "/tmp/catalog-demo/catalog-info.yaml" } });
    setup.exportArtifact.mockResolvedValueOnce({ outcome: "write-error", recovery: "blocked", diagnosticId: "export-conflict" });
    fireEvent.click(screen.getByRole("button", { name: "Export retained artifact" }));
    await screen.findByText(/Export was refused or could not be verified/);
    expect(screen.queryByText("Retained artifact exported")).toBeNull();
    setup.exportArtifact.mockResolvedValueOnce({ outcome: "saved", causalityRevision: 2, value: { status: "exported", fleet_run_id: submitted.fleet_run_id, repository_id: primary.repository_id, step_id: "catalog-generate.catalog", artifact_path: "catalog-info.yaml", destination: "/tmp/catalog-demo/catalog-info.yaml", sha256: CATALOG_FIXTURE_ARTIFACT_SHA, byte_count: 296, reference_id: CATALOG_FIXTURE_REFERENCE, cleanup_status: "failed", cleanup_failure: "STAGING_CLEANUP_FAILED" } });
    fireEvent.click(screen.getByRole("button", { name: "Export retained artifact" }));
    await screen.findByText("Retained artifact exported");
    expect(screen.getByText(/staging cleanup failed/)).toBeTruthy();
    expect(setup.exportArtifact).toHaveBeenLastCalledWith(submitted.fleet_run_id, { repository_id: primary.repository_id, step_id: "catalog-generate.catalog", path: "catalog-info.yaml", destination: "/tmp/catalog-demo/catalog-info.yaml" });
    fireEvent.click(screen.getByRole("button", { name: "Open original Game Tape" }));
    expect(setup.openRun).toHaveBeenCalledWith(submitted.fleet_run_id, primary.repository_id);
  });

  it.each([FleetRunStatus.FAILED, FleetRunStatus.CANCELLED])("keeps %s Run evidence available without exposing successful export", async (status) => {
    const setup = await fixture();
    completeRun(setup.client, status);
    await approveCandidate();
    fireEvent.click(screen.getByRole("button", { name: "Generate catalog" }));
    await screen.findByText(/This Run did not produce a successful catalog/);
    expect(screen.getByRole("button", { name: "Open original Game Tape" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Export retained artifact" })).toBeNull();
  });

  it("does not promote completed lifecycle with a failed command into catalog success", async () => {
    const setup = await fixture();
    completeRun(setup.client, FleetRunStatus.COMPLETED, true);
    await approveCandidate();
    fireEvent.click(screen.getByRole("button", { name: "Generate catalog" }));
    await screen.findByText(/This Run did not produce a successful catalog/);
    expect(screen.getByText("Run lifecycle").nextElementSibling).toHaveTextContent(/^COMPLETED$/);
    expect(screen.getByText("Recorded result").nextElementSibling).toHaveTextContent(/^ATTENTION_REQUIRED$/);
    expect(screen.getByRole("button", { name: "Open original Game Tape" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Export retained artifact" })).toBeNull();
  });

  it("requires a new exploration and approval after a refused Run", async () => {
    const setup = await fixture();
    setup.start.mockResolvedValue({ outcome: "stale", diagnosticId: "source-changed" });
    await approveCandidate();
    fireEvent.click(screen.getByRole("button", { name: "Generate catalog" }));
    await screen.findByText(/Run not started/);
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Export retained artifact" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Hashmarks evidence" })).toBeNull();
  });
});
