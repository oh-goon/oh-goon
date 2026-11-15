import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SecurityCenter } from "@/features/security/components/SecurityCenter";
import { operations, operationUrl } from "@generated/operations";

const sbomPath = Object.getOwnPropertyDescriptor(operations.fleet_sbom, "path")!;
afterEach(() => Object.defineProperty(operations.fleet_sbom, "path", sbomPath));

describe("SecurityCenter SBOM navigation", () => {
  it.each([false, true])("uses the generated SBOM path after contract changes: %s", (changedPath) => {
    if (changedPath) Object.defineProperty(operations.fleet_sbom, "path", {
      value: "/recorded-sboms/{fleet_run_id}/bundle",
    });
    const runId = "recorded run ?#snow";
    render(<SecurityCenter security={null} actionsDisabled={false} onRunRecipe={vi.fn()} onExplorePackage={vi.fn()} sbomRunId={runId} />);
    expect(screen.getByRole("link", { name: "Open latest SBOM bundle" })).toHaveAttribute(
      "href", `/api${operationUrl(operations.fleet_sbom.operationId, { path: { fleet_run_id: runId } })}`,
    );
  });
});
