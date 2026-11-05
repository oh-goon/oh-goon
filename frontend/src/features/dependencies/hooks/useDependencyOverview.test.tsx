import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiClientProvider } from "@/app/ApiClientProvider";
import type { DependencyEcosystem } from "@/domain/api/ApiClient";
import { useDependencyOverview } from "@/features/dependencies/hooks/useDependencyOverview";
import type { DependencyPackageOverview } from "@generated/models";
import { InMemoryApiClient } from "@test/InMemoryApiClient";
import { dependencyQueryIdentity } from "../../../../e2e/fixtures/dependencies";

function overview(ecosystem: DependencyEcosystem): DependencyPackageOverview {
  return { ecosystem, package_name: "shared", observed_repositories: 1, versions: [], freshness: { current: 1, no_matching_dependency: 0, unknown: 0, stale: 0, no_current_evidence: 0 } };
}

function Probe({ ecosystem, packageName = "shared" }: { ecosystem: DependencyEcosystem; packageName?: string }) {
  const resource = useDependencyOverview({ packageName, ecosystem });
  return <><span data-testid="status">{resource.status}</span><span data-testid="identity">{resource.data?.ecosystem}</span><span data-testid="package">{resource.data?.package_name}</span></>;
}

describe("dependency overview namespace ownership", () => {
  it("clears displayed PyPI data while the same npm name is loading", async () => {
    const client = new InMemoryApiClient();
    const pending = Promise.withResolvers<DependencyPackageOverview>();
    vi.spyOn(client, "dependencyOverview").mockImplementation(({ ecosystem }) => ecosystem === "pypi" ? Promise.resolve(overview("pypi")) : pending.promise);
    const view = render(<ApiClientProvider client={client}><Probe ecosystem="pypi" /></ApiClientProvider>);
    await waitFor(() => expect(screen.getByTestId("identity")).toHaveTextContent("pypi"));
    view.rerender(<ApiClientProvider client={client}><Probe ecosystem="npm" /></ApiClientProvider>);
    expect(screen.getByTestId("identity")).toBeEmptyDOMElement();
    await act(async () => { pending.resolve(overview("npm")); });
    await waitFor(() => expect(screen.getByTestId("identity")).toHaveTextContent("npm"));
  });

  it("ignores a late response for the other ecosystem with the same name", async () => {
    const client = new InMemoryApiClient();
    const pypi = Promise.withResolvers<DependencyPackageOverview>();
    const npm = Promise.withResolvers<DependencyPackageOverview>();
    vi.spyOn(client, "dependencyOverview").mockImplementation(({ ecosystem }) => ecosystem === "pypi" ? pypi.promise : npm.promise);
    const view = render(<ApiClientProvider client={client}><Probe ecosystem="pypi" /></ApiClientProvider>);
    view.rerender(<ApiClientProvider client={client}><Probe ecosystem="npm" /></ApiClientProvider>);
    await act(async () => { npm.resolve(overview("npm")); });
    await waitFor(() => expect(screen.getByTestId("identity")).toHaveTextContent("npm"));
    await act(async () => { pypi.resolve(overview("pypi")); });
    expect(screen.getByTestId("identity")).toHaveTextContent("npm");
  });

  it("rejects a response that repeats the name but changes the requested namespace", async () => {
    const client = new InMemoryApiClient();
    vi.spyOn(client, "dependencyOverview").mockResolvedValue(overview("pypi"));
    render(<ApiClientProvider client={client}><Probe ecosystem="npm" /></ApiClientProvider>);
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("read-error"));
    expect(screen.getByTestId("identity")).toBeEmptyDOMElement();
  });

  it("accepts a canonical name only through the backend-provided query correspondence", async () => {
    const client = new InMemoryApiClient();
    vi.spyOn(client, "dependencyOverview").mockResolvedValue({ ...overview("pypi"), package_name: "mlflow", query_identity: dependencyQueryIdentity("pypi", "MLFlow", "mlflow") });
    render(<ApiClientProvider client={client}><Probe ecosystem="pypi" packageName="MLFlow" /></ApiClientProvider>);
    await waitFor(() => expect(screen.getByTestId("package")).toHaveTextContent("mlflow"));
  });

  it.each([{ requested_name: "another-package" }, { canonical_name: "another-package" }, { ecosystem: "npm" as const }])("rejects query correspondence with a changed identity %j", async (changed) => {
    const client = new InMemoryApiClient();
    const correspondence = { ...dependencyQueryIdentity("pypi", "MLFlow", "mlflow"), ...changed };
    vi.spyOn(client, "dependencyOverview").mockResolvedValue({ ...overview("pypi"), package_name: "mlflow", query_identity: correspondence });
    render(<ApiClientProvider client={client}><Probe ecosystem="pypi" packageName="MLFlow" /></ApiClientProvider>);
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("read-error"));
    expect(screen.getByTestId("package")).toBeEmptyDOMElement();
  });
});
