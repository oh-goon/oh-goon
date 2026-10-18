import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";

import type { MissingPlanArtifact, PresentPlanArtifact } from "@generated/models";
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
