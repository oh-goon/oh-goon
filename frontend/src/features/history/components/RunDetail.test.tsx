import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RunDetail } from "@/features/history/components/RunDetail";
import { FleetRunStatus, GameTapeStatus } from "@generated/enums";
import { operations, operationUrl } from "@generated/operations";
import { runDetail, runState } from "../../../../e2e/fixtures/runProjection";
import { PRIMARY_REPOSITORY } from "../../../../e2e/fixtures/identities";
import { fixtureRunFeedback } from "../../../../e2e/fixtures/runFeedback";

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

describe("RunDetail canonical recovery actions", () => {
  it("uses backend readmission guidance instead of interpreting the failure code", () => {
    const detail = runDetail(runState("run-original", FleetRunStatus.FAILED, [PRIMARY_REPOSITORY], 1, 1), GameTapeStatus.FAILED, true);
    detail.feedback = fixtureRunFeedback("run-original", { recovery_action: "readmit-placement" });
    const onReadmit = vi.fn();
    render(<RunDetail detail={detail} summary={null} actionsDisabled={false} onRunAgain={vi.fn()} onReadmit={onReadmit} onOpenRecovery={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Retry placement" }));
    expect(onReadmit).toHaveBeenCalledWith("run-original");
    expect(screen.queryByRole("button", { name: /^Run .* again$/ })).toBeNull();
  });

  it.each(["denied", "missing", "foreign", "stale"] as const)("does not infer rerun permission when canonical feedback is %s", (condition) => {
    const detail = runDetail(runState("run-original", FleetRunStatus.FAILED, [PRIMARY_REPOSITORY], 1, 1), GameTapeStatus.FAILED, true);
    detail.rerun_supported = true;
    detail.feedback = fixtureRunFeedback(condition === "foreign" ? "another-run" : "run-original", { recovery_action: condition === "denied" ? "none" : "run-same-target" });
    if (condition === "missing") Reflect.deleteProperty(detail, "feedback");
    if (condition === "stale") detail.journal_revision = 204;
    render(<RunDetail detail={detail} summary={null} actionsDisabled={false} onRunAgain={vi.fn()} onReadmit={vi.fn()} onOpenRecovery={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /^Run .* again$/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry placement" })).toBeNull();
  });
});
