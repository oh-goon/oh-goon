import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiClientProvider } from "@/app/ApiClientProvider";
import { ScheduleCenter } from "@/features/schedules/components/ScheduleCenter";
import { ScheduleState } from "@generated/enums";
import type { ScheduleCatalog, ScheduleDefinition } from "@generated/models";
import { InMemoryApiClient } from "@test/InMemoryApiClient";

function definition(recordVersion: number): ScheduleDefinition {
  return {
    schedule_id: "nightly",
    generation_id: "generation-a",
    record_version: recordVersion,
    name: "Nightly",
    repository_ids: ["repo-a"],
    repository_instance_ids: { "repo-a": "instance-a" },
    target: { plan: { name: "security/standard" } },
    interval_minutes: 60,
    state: ScheduleState.ACTIVE,
  } as unknown as ScheduleDefinition;
}

function catalog(recordVersion: number): ScheduleCatalog {
  return {
    schedules: [{
      definition: definition(recordVersion),
      latest_occurrence: null,
      next_run_at: null,
      missing_expected_occurrence_at: null,
    }],
  } as unknown as ScheduleCatalog;
}

function center(client: InMemoryApiClient, recordVersion: number) {
  return <ApiClientProvider client={client}>
    <ScheduleCenter
      catalog={catalog(recordVersion)}
      actionsDisabled={false}
      onUpdate={vi.fn()}
      onDelete={vi.fn()}
      onRun={vi.fn()}
      onOpenRun={vi.fn()}
    />
  </ApiClientProvider>;
}

describe("ScheduleCenter destructive confirmation identity", () => {
  it("does not carry delete confirmation into a newer schedule incarnation", () => {
    const client = new InMemoryApiClient();
    const view = render(center(client, 1));

    fireEvent.click(screen.getByText("Manage schedule"));
    fireEvent.click(screen.getByRole("button", { name: "Remove Schedule" }));
    expect(screen.getByRole("button", { name: "Confirm removal" })).toBeTruthy();

    view.rerender(center(client, 2));

    expect(screen.queryByRole("button", { name: "Confirm removal" })).toBeNull();
    expect(screen.getByRole("button", { name: "Remove Schedule" })).toBeTruthy();
  });
});
