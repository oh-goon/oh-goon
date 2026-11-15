import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RunDetail } from "@/features/history/components/RunDetail";
import { FleetRunStatus, GameTapeStatus } from "@generated/enums";
import { operations, operationUrl } from "@generated/operations";
import { runDetail, runState } from "../../../../e2e/fixtures/runProjection";
import { PRIMARY_REPOSITORY } from "../../../../e2e/fixtures/identities";

const receiptPath = Object.getOwnPropertyDescriptor(operations.get_fleet_run_enforcer_receipt, "path")!;
afterEach(() => Object.defineProperty(operations.get_fleet_run_enforcer_receipt, "path", receiptPath));

describe("RunDetail receipt navigation", () => {
  it.each([false, true])("uses the generated receipt path after contract changes: %s", (changedPath) => {
    if (changedPath) Object.defineProperty(operations.get_fleet_run_enforcer_receipt, "path", {
      value: "/recorded-receipts/{fleet_run_id}/enforcer",
    });
    const runId = "recorded run ?#snow";
    const detail = runDetail(runState(runId, FleetRunStatus.COMPLETED, [PRIMARY_REPOSITORY], 1, 1), GameTapeStatus.PASSED, false);
    detail.game_tape.enforcer_receipt_available = true;
    render(<RunDetail detail={detail} summary={null} actionsDisabled={false} onRunAgain={vi.fn()} onReadmit={vi.fn()} onOpenRecovery={vi.fn()} />);
    expect(screen.getByRole("link", { name: "Download enforcer.yml" })).toHaveAttribute(
      "href", `/api${operationUrl(operations.get_fleet_run_enforcer_receipt.operationId, { path: { fleet_run_id: runId } })}`,
    );
  });
});
