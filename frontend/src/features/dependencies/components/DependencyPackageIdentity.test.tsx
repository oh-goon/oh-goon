import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { completeRead } from "@/domain/reads/ReadResource";
import { DependencySearchPanel } from "@/features/dependencies/components/DependencySearchPanel";
import { RepositoryDependenciesPanel } from "@/features/dependencies/components/RepositoryDependenciesPanel";
import type { DependencySearchResult, RepositoryDependencyPage } from "@generated/models";
import { dependencyMatches } from "../../../../e2e/fixtures/dependencies";

describe("dependency package identity callbacks", () => {
  it("opens same-name PyPI and npm search results with their own namespace", () => {
    const open = vi.fn();
    render(<DependencySearchPanel query="shared" onSearch={vi.fn()} onPage={vi.fn()} onOpenPackage={open} resource={completeRead<DependencySearchResult>({
      query: "shared", total: 3, offset: 0, limit: 30,
      items: [
        { ecosystem: "pypi", package_name: "shared", repositories: 1, resolved_versions: 1 },
        { ecosystem: "npm", package_name: "shared", repositories: 1, resolved_versions: 1 },
        { ecosystem: "npm", package_name: "@scope/shared", repositories: 1, resolved_versions: 1 },
      ],
    }, "fixture", 0, "2026-11-04T00:00:00Z")} />);
    fireEvent.click(screen.getByRole("button", { name: /^shared.*pypi/ }));
    fireEvent.click(screen.getByRole("button", { name: /^shared.*npm/ }));
    fireEvent.click(screen.getByRole("button", { name: /@scope\/shared.*npm/ }));
    expect(open.mock.calls).toEqual([
      [{ packageName: "shared", ecosystem: "pypi" }],
      [{ packageName: "shared", ecosystem: "npm" }],
      [{ packageName: "@scope/shared", ecosystem: "npm" }],
    ]);
  });

  it("keeps repository dependency callbacks and display rows ecosystem-qualified", () => {
    const base = dependencyMatches("mlflow")[0];
    if (base === undefined) throw new Error("Missing dependency fixture");
    const open = vi.fn();
    render(<RepositoryDependenciesPanel repositoryId={base.repository.repository_id} onBack={vi.fn()} onPage={vi.fn()} onOpenPackage={open} onOpenEvidence={vi.fn()} resource={completeRead<RepositoryDependencyPage>({
      repository: base.repository, total: 2, offset: 0, limit: 100,
      items: [{ ...base, package_name: "shared", ecosystem: "pypi" }, { ...base, package_name: "shared", ecosystem: "npm" }],
    }, "fixture", 0, "2026-11-04T00:00:00Z")} />);
    fireEvent.click(screen.getByRole("button", { name: /^shared.*npm/ }));
    fireEvent.click(screen.getByRole("button", { name: /^shared.*pypi/ }));
    expect(open.mock.calls).toEqual([
      [{ packageName: "shared", ecosystem: "npm" }], [{ packageName: "shared", ecosystem: "pypi" }],
    ]);
  });
});
