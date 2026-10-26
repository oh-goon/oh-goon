import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiClientProvider } from "@/app/ApiClientProvider";
import { ReadController } from "@/domain/concurrency/ReadController";
import type { LeagueMap, SharedPlanCatalog, SharedPlanDocument, SharedPlanLockPreview, SharedPlanScheduleCreateRequest } from "@generated/models";
import { ScheduleCreatePanel } from "@/features/schedules/components/ScheduleCreatePanel";
import { InMemoryApiClient } from "@test/InMemoryApiClient";

const LEAGUE = {
  summary: { configured_teams: 1, folders: 1, git_repositories: 1, invalid_goon_yml: 0, missing_goon_yml: 0 },
  teams: [{
    repository: { repository_id: "repo-a", name: "Repo A", path: "/work/repo-a" },
    repository_instance_id: "instance-repo-a",
    pipeline: { kind: "configured", name: "repo-a", source: "goon.yml" },
  }],
} as unknown as LeagueMap;

const PLANS = {
  plans: [{ plan_key: "security/standard", shared_plan_id: "shared-plan-a", sha256: "0".repeat(64), changed_since_last_run: false, last_run: null }],
} as unknown as SharedPlanCatalog;

function planDocument(inputs: SharedPlanDocument["inputs"] = []): SharedPlanDocument {
  return {
    plan_key: "security/standard",
    shared_plan_id: "shared-plan-a",
    sha256: "0".repeat(64),
    changed_since_last_run: false,
    last_run: null,
    content: "version: 1\n",
    inputs,
  } as unknown as SharedPlanDocument;
}

class SchedulePlanApiClient extends InMemoryApiClient {
  readonly lockPreviewRequests: Array<{ planKey: string; expectedRevision: string; inputs: Record<string, string> }> = [];

  constructor(
    private document: SharedPlanDocument,
    private readonly validationError: Error | null = null,
  ) {
    super();
  }

  replaceDocument(document: SharedPlanDocument): void {
    this.document = document;
  }

  override async sharedPlan(planKey: string): Promise<SharedPlanDocument> {
    if (planKey !== this.document.plan_key) throw new Error(`Unknown test Plan ${planKey}`);
    return structuredClone(this.document);
  }

  override async previewSharedPlanLock(
    planKey: string,
    expectedRevision: string,
    inputs: Record<string, string> = {},
  ): Promise<SharedPlanLockPreview> {
    this.lockPreviewRequests.push({ planKey, expectedRevision, inputs: { ...inputs } });
    if (this.validationError !== null) throw this.validationError;
    return { plan_key: planKey, plan_id: "shared-plan-a", sha256: expectedRevision, execution_digest: "sha256:" + "d".repeat(64), content: "lock_version: 2\n" };
  }
}

type ScheduleCreateHandler = (scheduleId: string, request: SharedPlanScheduleCreateRequest) => Promise<boolean>;

function panel(
  client: SchedulePlanApiClient,
  onCreate: ScheduleCreateHandler,
  league: LeagueMap = LEAGUE,
  plans: SharedPlanCatalog = PLANS,
  onOpenPlans?: () => void,
  onCreated?: () => void,
) {
  return <ApiClientProvider client={client}>
    <ScheduleCreatePanel league={league} plans={plans} actionsDisabled={false} onCreate={onCreate} onOpenPlans={onOpenPlans} onCreated={onCreated} />
  </ApiClientProvider>;
}

describe("ScheduleCreatePanel", () => {
  it("keeps browser authoring human-first while submitting backend-owned Plan intent", async () => {
    const onCreate = vi.fn<ScheduleCreateHandler>().mockResolvedValue(true);
    const client = new SchedulePlanApiClient(planDocument());
    render(panel(client, onCreate));

    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Nightly security" } });
    expect(screen.getByText("nightly-security")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Create Schedule" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Plan"), { target: { value: "security/standard" } });
    fireEvent.click(screen.getByLabelText("Repo A"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Create Schedule" })).toBeEnabled());
    expect(screen.getByText("This Schedule will be created enabled. You can pause it after creation.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Frequency"), { target: { value: "custom" } });
    fireEvent.change(screen.getByLabelText("Interval (minutes)"), { target: { value: "120" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Schedule" }));

    await waitFor(() => expect(onCreate).toHaveBeenCalledWith("nightly-security", {
      name: "Nightly security",
      repository_ids: ["repo-a"],
      expected_repository_instance_ids: { "repo-a": "instance-repo-a" },
      plan_key: "security/standard",
      expected_plan_revision: "0".repeat(64),
      expected_plan_id: "shared-plan-a",
      expected_execution_digest: "sha256:" + "d".repeat(64),
      interval_minutes: 120,
      state: "active",
      inputs: {},
    }));
  });

  it("requires and submits the selected Plan input contract instead of silently sending empty inputs", async () => {
    const onCreate = vi.fn<ScheduleCreateHandler>().mockResolvedValue(true);
    const client = new SchedulePlanApiClient(planDocument([{
      name: "severity",
      input_type: "choice",
      default: null,
      required: true,
      description: "Minimum severity",
      choices: ["high", "critical"],
      minimum: null,
      maximum: null,
      non_empty: false,
      pattern: null,
      starts_with: null,
    }] as SharedPlanDocument["inputs"]));
    render(panel(client, onCreate));

    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Nightly security" } });
    fireEvent.change(screen.getByLabelText("Plan"), { target: { value: "security/standard" } });
    fireEvent.click(screen.getByLabelText("Repo A"));

    await waitFor(() => expect(screen.getByLabelText(/severity/)).toBeTruthy());
    expect(screen.getByRole("button", { name: "Create Schedule" })).toBeDisabled();
    expect(screen.getByText("Complete the required Plan inputs before creating the Schedule.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/severity/), { target: { value: "critical" } });
    await waitFor(() => expect(client.lockPreviewRequests).toContainEqual({ planKey: "security/standard", expectedRevision: "0".repeat(64), inputs: { severity: "critical" } }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Create Schedule" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Create Schedule" }));

    await waitFor(() => expect(onCreate).toHaveBeenCalledWith("nightly-security", expect.objectContaining({
      plan_key: "security/standard",
      expected_plan_revision: "0".repeat(64),
      expected_plan_id: "shared-plan-a",
      inputs: { severity: "critical" },
    })));
  });

  it("keeps Schedule creation disabled when backend Plan-input validation rejects the values", async () => {
    const onCreate = vi.fn<ScheduleCreateHandler>().mockResolvedValue(true);
    const client = new SchedulePlanApiClient(planDocument([{
      name: "severity",
      input_type: "choice",
      default: null,
      required: true,
      description: "Minimum severity",
      choices: ["high", "critical"],
      minimum: null,
      maximum: null,
      non_empty: false,
      pattern: null,
      starts_with: null,
    }] as SharedPlanDocument["inputs"]), new Error("invalid input"));
    render(panel(client, onCreate));

    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Nightly security" } });
    fireEvent.change(screen.getByLabelText("Plan"), { target: { value: "security/standard" } });
    fireEvent.click(screen.getByLabelText("Repo A"));
    await waitFor(() => expect(screen.getByLabelText(/severity/)).toBeTruthy());
    fireEvent.change(screen.getByLabelText(/severity/), { target: { value: "critical" } });
    // M215: make input ownership and backend invocation explicit before
    // checking the validation alert. A reset-to-empty must fail at its cause.
    await waitFor(() => expect(screen.getByLabelText(/severity/)).toHaveValue("critical"));
    await waitFor(() => expect(client.lockPreviewRequests).toContainEqual({
      planKey: "security/standard", expectedRevision: "0".repeat(64), inputs: { severity: "critical" },
    }));

    const validationAlert = await screen.findByRole("alert", {}, { timeout: 3_000 });
    expect(validationAlert).toHaveTextContent("The backend could not resolve these Plan inputs. Review the values before creating the Schedule.");
    expect(screen.getByRole("button", { name: "Create Schedule" })).toBeDisabled();
    expect(onCreate).not.toHaveBeenCalled();
  });

  it("preserves edited inputs on a refreshed revision, but resets on a changed revision", async () => {
    const requiredInputs = [{
      name: "severity", input_type: "choice", default: null, required: true,
      description: "Minimum severity", choices: ["high", "critical"],
      minimum: null, maximum: null, non_empty: false, pattern: null, starts_with: null,
    }] as SharedPlanDocument["inputs"];
    const client = new SchedulePlanApiClient(planDocument(requiredInputs), new Error("invalid input"));
    const reads = new ReadController();
    const onCreate = vi.fn<ScheduleCreateHandler>().mockResolvedValue(true);
    render(<ApiClientProvider client={client} readController={reads}>
      <ScheduleCreatePanel league={LEAGUE} plans={PLANS} actionsDisabled={false} onCreate={onCreate} />
    </ApiClientProvider>);
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Nightly security" } });
    fireEvent.change(screen.getByLabelText("Plan"), { target: { value: "security/standard" } });
    fireEvent.click(screen.getByLabelText("Repo A"));
    await waitFor(() => expect(screen.getByLabelText(/severity/)).toBeEnabled());
    fireEvent.change(screen.getByLabelText(/severity/), { target: { value: "critical" } });
    await waitFor(() => expect(client.lockPreviewRequests).toContainEqual({
      planKey: "security/standard", expectedRevision: "0".repeat(64), inputs: { severity: "critical" },
    }));
    await screen.findByRole("alert");

    // Same exact Plan authority re-fetch: delayed defaults must not erase the edit.
    await act(async () => { reads.invalidate("shared-plan-document"); });
    await waitFor(() => expect(screen.getByLabelText(/severity/)).toHaveValue("critical"));
    await waitFor(() => expect(client.lockPreviewRequests.at(-1)?.inputs).toEqual({ severity: "critical" }));
    expect(onCreate).not.toHaveBeenCalled();

    // A new SHA belongs to a new input contract. Do not inherit the old draft.
    client.replaceDocument({ ...planDocument(requiredInputs), sha256: "1".repeat(64) });
    await act(async () => { reads.invalidate("shared-plan-document"); });
    await waitFor(() => expect(screen.getByLabelText(/severity/)).toHaveValue(""));
    expect(screen.getByRole("button", { name: "Create Schedule" })).toBeDisabled();
    expect(onCreate).not.toHaveBeenCalled();
  });

  it("does not settle an older create into a newer edited draft", async () => {
    let resolveCreate!: (saved: boolean) => void;
    const pending = new Promise<boolean>((resolve) => { resolveCreate = resolve; });
    const onCreate = vi.fn<ScheduleCreateHandler>().mockReturnValue(pending);
    const onCreated = vi.fn();
    const client = new SchedulePlanApiClient(planDocument());
    render(panel(client, onCreate, LEAGUE, PLANS, undefined, onCreated));

    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Nightly security" } });
    fireEvent.change(screen.getByLabelText("Plan"), { target: { value: "security/standard" } });
    fireEvent.click(screen.getByLabelText("Repo A"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Create Schedule" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Create Schedule" }));
    await waitFor(() => expect(onCreate).toHaveBeenCalledOnce());

    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Newer draft" } });
    resolveCreate(true);

    await waitFor(() => expect(screen.getByLabelText("Display name")).toHaveValue("Newer draft"));
    expect(onCreated).not.toHaveBeenCalled();
  });

  it("drops repository and Plan selections that disappear from the current backend catalogs", async () => {
    const onCreate = vi.fn<ScheduleCreateHandler>().mockResolvedValue(true);
    const client = new SchedulePlanApiClient(planDocument());
    const view = render(panel(client, onCreate));

    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Nightly security" } });
    expect(screen.getByRole("button", { name: "Create Schedule" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Plan"), { target: { value: "security/standard" } });
    fireEvent.click(screen.getByLabelText("Repo A"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Create Schedule" })).toBeEnabled());

    const onOpenPlans = vi.fn();
    view.rerender(panel(
      client,
      onCreate,
      { ...LEAGUE, teams: [] } as LeagueMap,
      { plans: [] } as unknown as SharedPlanCatalog,
      onOpenPlans,
    ));

    await waitFor(() => expect(screen.getByRole("button", { name: "Create Schedule" })).toBeDisabled());
    expect(screen.getByText("No runnable repositories yet")).toBeTruthy();
    expect(screen.getByText("No saved Plans yet")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Open Plans" }));
    expect(onOpenPlans).toHaveBeenCalledOnce();
  });
});
