import { fireEvent, render, screen, within } from "@testing-library/react";
import { vi } from "vitest";

import { ApiClientProvider } from "@/app/ApiClientProvider";
import { GameTape } from "@/features/game-tape/GameTapeFeature";
import type { RunFeedback } from "@generated/models";
import { InMemoryApiClient } from "@test/InMemoryApiClient";
import { PRIMARY_REPOSITORY } from "../../../../e2e/fixtures/identities";
import { runDetail, runState } from "../../../../e2e/fixtures/runProjection";

describe("Game Tape original Run failure", () => {
  it.each(["readmit-placement", "run-same-target", "open-recovery", "none"] as const)("preserves the canonical failure independently of the %s next-action hint", (action: RunFeedback["recovery_action"]) => {
    const detail = runDetail(runState("failed-original", "FAILED", [PRIMARY_REPOSITORY], 1, 1), "FAILED", true);
    detail.feedback.recovery_action = action;
    const onOpenRunActions = vi.fn();
    const onOpenRecovery = vi.fn();
    render(<ApiClientProvider client={new InMemoryApiClient()}><GameTape detail={detail} onOpenRunActions={onOpenRunActions} onOpenRecovery={onOpenRecovery} /></ApiClientProvider>);
    const recovery = screen.getByRole("region", { name: "Recovery" });
    expect(within(recovery).getByText(/deliberately long failure message/)).toBeVisible();
    if (action === "none") {
      expect(within(recovery).queryByRole("button")).toBeNull();
      expect(within(recovery).queryByText("Safe next action")).toBeNull();
    } else {
      fireEvent.click(within(recovery).getByRole("button", { name: action === "open-recovery" ? "Open Recovery" : "Open run actions" }));
      expect(action === "open-recovery" ? onOpenRecovery : onOpenRunActions).toHaveBeenCalledOnce();
    }
  });

  it.each(["missing", "foreign"] as const)("retains failure evidence when the continuation hint is %s", (condition) => {
    const detail = runDetail(runState("failed-original", "FAILED", [PRIMARY_REPOSITORY], 1, 1), "FAILED", true);
    if (condition === "missing") Reflect.deleteProperty(detail, "feedback");
    else detail.feedback.fleet_run_id = "another-run";
    render(<ApiClientProvider client={new InMemoryApiClient()}><GameTape detail={detail} onOpenRunActions={vi.fn()} onOpenRecovery={vi.fn()} /></ApiClientProvider>);
    const recovery = screen.getByRole("region", { name: "Recovery" });
    expect(within(recovery).getByText(/deliberately long failure message/)).toBeVisible();
    expect(within(recovery).queryByRole("button")).toBeNull();
  });
});
