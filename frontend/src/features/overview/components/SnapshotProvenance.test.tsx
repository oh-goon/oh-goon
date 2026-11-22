import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import type { FleetSnapshot } from "@generated/models";
import { SnapshotProvenance } from "@/features/overview/components/SnapshotProvenance";

it("retains policy provenance from both active Runs", () => {
  const first = { operations: [{ evidence: { policy: { policy_id: "policy-a" } } }] };
  const second = { operations: [{ evidence: { policy: { policy_id: "policy-b" } } }] };
  const fleet = {
    provenance: { snapshot_id: "source-snapshot", generated_at: "2026-11-22T12:00:00Z", durable_revision: 7, journal_revision: 5 },
    repositories: [],
    latest_fleet_runs: [],
    active_fleet_runs: [first, second],
    active_fleet_run: first,
  } as unknown as FleetSnapshot;
  render(<SnapshotProvenance fleet={fleet} />);
  expect(screen.getByTitle(/policy-a.*policy-b/s)).toHaveTextContent("2");
});
