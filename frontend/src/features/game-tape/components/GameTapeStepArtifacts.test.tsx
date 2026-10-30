import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";

import type { GameTapeArtifactAvailability, GameTapeArtifactObservation, MissingPlanArtifact, PresentPlanArtifact } from "@generated/models";
import { GameTapeStepArtifacts } from "@/features/game-tape/components/GameTapeStepArtifacts";

const retained: PresentPlanArtifact = {
  status: "PRESENT",
  path: "reports/junit.xml",
  sha256: "a".repeat(64),
  byte_count: 4096,
  retained_object: {
    kind: "file",
    sha256: "b".repeat(64),
    byte_count: 4096,
    entry_count: 1,
    object_id: `file-sha256-${"b".repeat(64)}`,
    reference_id: `ref-${"c".repeat(32)}`,
    owner_repository_id: `repo-${"d".repeat(20)}`,
  },
};
const missing: MissingPlanArtifact = { status: "MISSING", path: "coverage/index.html" };
const artifactOwner = { repository_execution_id: "repo-execution", repository_id: `repo-${"d".repeat(20)}`, step_execution_id: "step-execution", step_id: "train" };

function observation(patch: Partial<GameTapeArtifactAvailability> = {}): GameTapeArtifactObservation {
  return {
    schema_name: "oh-goon.artifact-availability/v1", fleet_run_id: "recorded-run", journal_revision: 17,
    started_at: "2026-10-28T12:00:00Z", finished_at: "2026-10-28T12:00:01Z",
    artifact_limit: 2, byte_budget: 8192, time_budget_seconds: 1, total_artifacts: 1, omitted_artifacts: 0, bytes_scheduled: 4096,
    observations: [{
      ...artifactOwner, path: retained.path, recorded_status: "PRESENT", recorded_sha256: retained.sha256,
      recorded_byte_count: retained.byte_count, reference_id: retained.retained_object!.reference_id,
      status: "AVAILABLE", reason: "VERIFIED", checked_at: "2026-10-28T12:00:01Z", staging_cleanup: "VERIFIED",
      ...patch,
    }],
  };
}

describe("GameTapeStepArtifacts", () => {
  it("keeps artifact identity under the producing Step and discloses exact retained evidence", () => {
    render(<GameTapeStepArtifacts artifacts={[retained, missing]} />);
    expect(screen.getAllByText("reports/junit.xml")).toHaveLength(2);
    expect(screen.getByText("coverage/index.html")).toBeTruthy();
    expect(screen.getByText("Declared artifact was not retained")).toBeTruthy();
    fireEvent.click(screen.getByText("Artifact evidence"));
    expect(screen.getByText("a".repeat(64))).toBeTruthy();
    expect(screen.getByText(`ref-${"c".repeat(32)}`)).toBeTruthy();
    expect(screen.getByText(`repo-${"d".repeat(20)}`)).toBeTruthy();
    expect(screen.getByText(/Game Tape records artifact evidence/)).toBeTruthy();
  });

  it("copies only canonical digest and retained reference facts", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<GameTapeStepArtifacts artifacts={[retained]} />);

    fireEvent.click(screen.getByRole("button", { name: "Copy SHA-256" }));
    expect(await screen.findByRole("button", { name: "SHA-256 copied" })).toBeTruthy();
    expect(writeText).toHaveBeenCalledWith("a".repeat(64));

    fireEvent.click(screen.getByRole("button", { name: "Copy retained reference" }));
    expect(await screen.findByRole("button", { name: "Reference copied" })).toBeTruthy();
    expect(writeText).toHaveBeenCalledWith(`ref-${"c".repeat(32)}`);
  });

  it("separates recorded retention from the backend's observed byte availability", () => {
    const view = render(<GameTapeStepArtifacts artifacts={[retained]} artifactOwner={artifactOwner} />);
    expect(screen.getByText("Recorded retained reference")).toBeTruthy();
    expect(screen.getByText("Current byte availability has not been checked for this record.")).toBeTruthy();
    expect(screen.queryByText("Available when checked")).toBeNull();

    view.rerender(<GameTapeStepArtifacts artifacts={[retained]} artifactOwner={artifactOwner} observation={observation()} />);
    expect(screen.getByText("Available when checked")).toBeTruthy();
    expect(screen.getByText("Retained bytes matched the recorded identity.")).toBeTruthy();

    view.rerender(<GameTapeStepArtifacts artifacts={[retained]} artifactOwner={artifactOwner} observation={observation({ status: "UNAVAILABLE", reason: "REFERENCE_UNAVAILABLE" })} />);
    expect(screen.getByText("Unavailable when checked")).toBeTruthy();
    expect(screen.getByText("a".repeat(64))).toBeTruthy();
    expect(screen.getByText(retained.retained_object!.reference_id)).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it.each<Partial<GameTapeArtifactAvailability>>([
    { repository_id: "another-repository" },
    { repository_execution_id: "another-repository-execution" },
    { step_execution_id: "another-step-execution" },
    { step_id: "another-step" },
    { path: "other.json" },
    { recorded_sha256: "f".repeat(64) },
    { recorded_byte_count: 4097 },
    { reference_id: "another-reference" },
  ])("does not attach a byte observation to different recorded evidence: %j", (changed) => {
    const view = render(<GameTapeStepArtifacts artifacts={[retained]} artifactOwner={artifactOwner} observation={observation()} />);
    expect(screen.getByText("Available when checked")).toBeTruthy();
    view.rerender(<GameTapeStepArtifacts artifacts={[retained]} artifactOwner={artifactOwner} observation={observation(changed)} />);
    expect(screen.queryByText("Available when checked")).toBeNull();
    expect(screen.getByText("Current byte availability has not been checked for this record.")).toBeTruthy();
  });

  it("renders refused, bounded and cleanup facts without changing the artifact evidence", () => {
    const view = render(<GameTapeStepArtifacts artifacts={[retained]} artifactOwner={artifactOwner} observation={observation({ status: "REFUSED", reason: "PRODUCER_UNSETTLED", staging_cleanup: "NOT_REQUIRED" })} />);
    expect(screen.getByText("Observation refused")).toBeTruthy();
    view.rerender(<GameTapeStepArtifacts artifacts={[retained]} artifactOwner={artifactOwner} observation={observation({ status: "UNOBSERVED", reason: "BYTE_BUDGET", staging_cleanup: "NOT_REQUIRED" })} />);
    expect(screen.getByText("Not observed")).toBeTruthy();
    expect(screen.getByText("The artifact exceeded the remaining byte budget.")).toBeTruthy();
    view.rerender(<GameTapeStepArtifacts artifacts={[retained]} artifactOwner={artifactOwner} observation={observation({ staging_cleanup: "FAILED", cleanup_failure: "STAGING_CLEANUP_FAILED" })} />);
    expect(screen.getByText("Available when checked")).toBeTruthy();
    expect(screen.getByText("The byte check reported a staging cleanup failure.")).toBeTruthy();
    expect(screen.getByText("a".repeat(64))).toBeTruthy();
  });
  it("does not attach a late copy completion to another artifact owner", async () => {
    let resolveCopy: () => void = () => {};
    const writeText = vi.fn(() => new Promise<void>((resolve) => { resolveCopy = resolve; }));
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const { rerender } = render(<GameTapeStepArtifacts artifacts={[retained]} ownerIdentity="step-a" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy SHA-256" }));
    rerender(<GameTapeStepArtifacts artifacts={[retained]} ownerIdentity="step-b" />);
    resolveCopy();
    await Promise.resolve();
    expect(screen.queryByRole("button", { name: "SHA-256 copied" })).toBeNull();
  });

});
