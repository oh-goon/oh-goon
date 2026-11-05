import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiClientProvider } from "@/app/ApiClientProvider";
import { DependenciesPage } from "@/app/pages/DependenciesPage";
import { i18n } from "@/i18n/setup";
import type { DependencyPackageOverview, DependencyRepositoryPage } from "@generated/models";
import { InMemoryApiClient } from "@test/InMemoryApiClient";
import { dependencyMatches, dependencyQueryIdentity } from "../../../e2e/fixtures/dependencies";

function packageClient() {
  const client = new InMemoryApiClient();
  const base = dependencyMatches("mlflow")[0];
  if (base === undefined) throw new Error("Missing dependency fixture");
  vi.spyOn(client, "dependencyOverview").mockImplementation(async ({ packageName, ecosystem = "pypi" }): Promise<DependencyPackageOverview> => ({
    package_name: packageName, ecosystem, observed_repositories: 1, versions: [],
    freshness: { current: 1, no_matching_dependency: 0, unknown: 0, stale: 0, no_current_evidence: 0 },
  }));
  vi.spyOn(client, "dependencyRepositories").mockImplementation(async ({ packageName, ecosystem = "pypi" }): Promise<DependencyRepositoryPage> => ({
    package_name: packageName, ecosystem, population_id: `${ecosystem}:${packageName}`,
    total: 1, offset: 0, limit: 25, items: [{ ...base, package_name: packageName, ecosystem }],
  }));
  return client;
}

describe("dependency page ecosystem identity", () => {
  it("navigates a same-name npm result with its namespace in URL state", async () => {
    window.history.replaceState({}, "", "#dependencies?q=shared");
    const client = packageClient();
    vi.spyOn(client, "dependencySearch").mockResolvedValue({ query: "shared", offset: 0, limit: 30, total: 2, items: [
      { package_name: "shared", ecosystem: "pypi", repositories: 1, resolved_versions: 1 },
      { package_name: "shared", ecosystem: "npm", repositories: 1, resolved_versions: 1 },
    ] });
    const navigate = vi.fn();
    render(<ApiClientProvider client={client}><DependenciesPage navigate={navigate} /></ApiClientProvider>);
    fireEvent.click(await screen.findByRole("button", { name: /^shared.*npm/ }));
    expect(navigate).toHaveBeenCalledWith("dependencies", { q: "shared", package: "shared", ecosystem: "npm" });
  });

  it.each(["npm", undefined] as const)("materializes the backend-qualified namespace for %s", async (ecosystem) => {
    window.history.replaceState({}, "", `#dependencies?package=shared${ecosystem ? `&ecosystem=${ecosystem}` : ""}`);
    const client = packageClient();
    const materialize = vi.spyOn(client, "materializeDependencyTargets").mockImplementation(async (request) => ({
      outcome: "saved", causalityRevision: 1,
      value: { query_id: request.expected_population_id, filter: request.filter, selected_at: "2026-11-04T00:00:00Z", total: 1, repo_ids: ["repo"], repository_instance_ids: { repo: "instance" } },
    }));
    const navigate = vi.fn();
    render(<ApiClientProvider client={client}><DependenciesPage navigate={navigate} /></ApiClientProvider>);
    fireEvent.click(await screen.findByRole("button", { name: i18n.t("dependencies.selectAllMatching", { count: 1 }) }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("shared-runs", { selection: `${ecosystem ?? "pypi"}:shared` }));
    expect(materialize.mock.calls[0]?.[0].filter).toMatchObject({ package_name: "shared", ecosystem: ecosystem ?? "pypi" });
  });

  it("rejects materialization that changes only the selected ecosystem", async () => {
    window.history.replaceState({}, "", "#dependencies?package=shared&ecosystem=npm");
    const client = packageClient();
    const materialize = vi.spyOn(client, "materializeDependencyTargets").mockImplementation(async (request) => ({
      outcome: "saved", causalityRevision: 1,
      value: { query_id: request.expected_population_id, filter: { ...request.filter, ecosystem: "pypi" }, selected_at: "2026-11-04T00:00:00Z", total: 1, repo_ids: ["repo"], repository_instance_ids: { repo: "instance" } },
    }));
    const navigate = vi.fn();
    render(<ApiClientProvider client={client}><DependenciesPage navigate={navigate} /></ApiClientProvider>);
    fireEvent.click(await screen.findByRole("button", { name: i18n.t("dependencies.selectAllMatching", { count: 1 }) }));
    await waitFor(() => expect(materialize).toHaveBeenCalledOnce());
    expect(navigate).not.toHaveBeenCalled();
  });

  it("does not carry a PyPI advisory draft into the same-name npm package", async () => {
    window.history.replaceState({}, "", "#dependencies?package=shared&ecosystem=pypi");
    const client = packageClient();
    const navigate = vi.fn();
    const view = render(<ApiClientProvider client={client}><DependenciesPage navigate={navigate} /></ApiClientProvider>);
    const advisoryLabel = i18n.t("dependencies.advisoryId");
    fireEvent.change(await screen.findByLabelText(advisoryLabel), { target: { value: "CVE-PYPI" } });
    window.history.replaceState({}, "", "#dependencies?package=shared&ecosystem=npm");
    view.rerender(<ApiClientProvider client={client}><DependenciesPage navigate={navigate} /></ApiClientProvider>);
    expect(screen.getByLabelText(advisoryLabel)).toHaveValue("");
    await screen.findByRole("button", { name: i18n.t("dependencies.selectAllMatching", { count: 1 }) });
  });

  it("materializes the producer-corresponding canonical name from a legacy alias URL", async () => {
    window.history.replaceState({}, "", "#dependencies?package=MLFlow&ecosystem=pypi");
    const client = packageClient();
    const queryIdentity = dependencyQueryIdentity("pypi", "MLFlow", "mlflow");
    const match = dependencyMatches("mlflow")[0];
    if (match === undefined) throw new Error("Missing dependency fixture");
    vi.spyOn(client, "dependencyOverview").mockResolvedValue({ package_name: "mlflow", ecosystem: "pypi", query_identity: queryIdentity, observed_repositories: 1, versions: [], freshness: { current: 1, unknown: 0, stale: 0, no_current_evidence: 0, no_matching_dependency: 0 } });
    vi.spyOn(client, "dependencyRepositories").mockResolvedValue({ package_name: "mlflow", ecosystem: "pypi", query_identity: queryIdentity, population_id: "canonical-population", total: 1, offset: 0, limit: 25, items: [match] });
    const materialize = vi.spyOn(client, "materializeDependencyTargets").mockImplementation(async (request) => ({ outcome: "saved", causalityRevision: 1, value: { query_id: request.expected_population_id, filter: request.filter, selected_at: "2026-11-04T00:00:00Z", total: 1, repo_ids: ["repo"], repository_instance_ids: { repo: "instance" } } }));
    const navigate = vi.fn();
    render(<ApiClientProvider client={client}><DependenciesPage navigate={navigate} /></ApiClientProvider>);
    fireEvent.click(await screen.findByRole("button", { name: i18n.t("dependencies.selectAllMatching", { count: 1 }) }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("shared-runs", { selection: "canonical-population" }));
    expect(materialize.mock.calls[0]?.[0].filter).toMatchObject({ package_name: "mlflow", ecosystem: "pypi" });
  });
});
