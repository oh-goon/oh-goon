import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiClientProvider } from "@/app/ApiClientProvider";
import type { PresentPlanArtifact, RunArtifactExport } from "@generated/models";
import { InMemoryApiClient } from "@test/InMemoryApiClient";
import { RunArtifactExportForm } from "@/features/game-tape/components/RunArtifactExportForm";

const artifact: PresentPlanArtifact = { status: "PRESENT", path: "catalog-info.yaml", sha256: "a".repeat(64), byte_count: 20, retained_object: { kind: "file", sha256: "b".repeat(64), object_id: `file-sha256-${"b".repeat(64)}`, byte_count: 20, entry_count: 1, reference_id: "ref-recorded", owner_repository_id: "repo-original" } };
const receipt: RunArtifactExport = { status: "exported", fleet_run_id: "original-run", repository_id: "repo-original", step_id: "original-step", artifact_path: artifact.path, destination: "/tmp/catalog-demo/original.yaml", sha256: artifact.sha256, byte_count: artifact.byte_count, reference_id: "ref-recorded", cleanup_status: "verified" };

describe("recorded artifact export", () => {
  it("does not report a receipt for different recorded bytes as verified export", async () => {
    const client = new InMemoryApiClient();
    const publish = vi.spyOn(client, "exportRunArtifact").mockResolvedValue({ outcome: "saved", causalityRevision: 1, value: { ...receipt, sha256: "c".repeat(64) } });
    render(<ApiClientProvider client={client}><RunArtifactExportForm key="recorded-artifact" fleetRunId="original-run" repositoryId="repo-original" stepId="original-step" artifact={artifact} /></ApiClientProvider>);
    fireEvent.change(screen.getByRole("textbox", { name: "Export destination" }), { target: { value: receipt.destination } });
    fireEvent.click(screen.getByRole("button", { name: "Export retained artifact" }));
    await screen.findByRole("alert");
    expect(publish).toHaveBeenCalledWith("original-run", { repository_id: "repo-original", step_id: "original-step", path: artifact.path, destination: receipt.destination });
    expect(screen.queryByText("Retained artifact exported")).toBeNull();
  });

  it("drops destination and late receipt when the recorded Run changes", async () => {
    const client = new InMemoryApiClient();
    let complete: ((value: Awaited<ReturnType<typeof client.exportRunArtifact>>) => void) | undefined;
    vi.spyOn(client, "exportRunArtifact").mockImplementation(() => new Promise((resolve) => { complete = resolve; }));
    const view = render(<ApiClientProvider client={client}><RunArtifactExportForm fleetRunId="original-run" repositoryId="repo-original" stepId="original-step" artifact={artifact} /></ApiClientProvider>);
    fireEvent.change(screen.getByRole("textbox", { name: "Export destination" }), { target: { value: receipt.destination } });
    fireEvent.click(screen.getByRole("button", { name: "Export retained artifact" }));
    view.rerender(<ApiClientProvider client={client}><RunArtifactExportForm fleetRunId="different-run" repositoryId="repo-original" stepId="original-step" artifact={artifact} /></ApiClientProvider>);
    await act(async () => { complete?.({ outcome: "saved", causalityRevision: 1, value: receipt }); });
    expect(screen.getByRole("textbox", { name: "Export destination" })).toHaveValue("");
    expect(screen.queryByText("Retained artifact exported")).toBeNull();
  });
});
