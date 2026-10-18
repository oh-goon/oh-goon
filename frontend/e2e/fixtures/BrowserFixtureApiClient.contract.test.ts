import { FleetRepositoryScope } from "@generated/enums";

import { BrowserFixtureApiClient } from "../BrowserFixtureApiClient";
import { FIXTURE_REPOSITORIES, FIXTURE_REPOSITORY_LIST } from "./identities";
import { FIXTURE_PLANS, FIXTURE_SHA } from "./plans";
import { DEFAULT_RUN_ID } from "./runIds";

describe("BrowserFixtureApiClient canonical fixture contracts", () => {
  it("scripts T5/T6 inspection envelopes without granting execution authority", async () => {
    const client = new BrowserFixtureApiClient();
    const document = FIXTURE_PLANS.document("python-quality");
    const rejected = await client.inspectSharedPlanInputs("python-quality", document.sha256, { coverage_threshold: "101" });
    expect(rejected).toMatchObject({
      schema: "oh-goon.shared-plan-input-inspection/v1", status: "rejected",
      plan_key: null, sha256: null, plan_id: null, execution: "not_started_by_inspection",
      diagnostic: { code: "INVALID_INPUT_VALUE", inputs: ["coverage_threshold"], execution: "not_started_by_diagnostic" },
    });
    const accepted = await client.inspectSharedPlanInputs("python-quality", document.sha256, { coverage_threshold: "85" });
    expect(accepted).toMatchObject({ status: "accepted", plan_key: document.plan_key, sha256: document.sha256, diagnostic: null, execution: "not_started_by_inspection" });
    await expect(client.inspectSharedPlanInputs("python-quality", "a".repeat(64), { coverage_threshold: "85" })).rejects.toThrow("revision is stale");
  });

  it("seeds default Fleet authority for Run-history mutations", async () => {
    const client = new BrowserFixtureApiClient();
    const fleet = await client.fleet();
    const includedIds = new Set(
      (fleet.repositories ?? [])
        .filter((item) => item.scope === FleetRepositoryScope.INCLUDED)
        .map((item) => item.repository.repository_id),
    );
    const source = await client.fleetRunDetail(DEFAULT_RUN_ID);

    expect(fleet.summary.included).toBe(FIXTURE_REPOSITORY_LIST.length);
    expect(source.run.targets.every((target) => includedIds.has(target.repository_id))).toBe(true);
    expect(source.repository_admissions.map((item) => item.repository_id)).toEqual(
      source.run.targets.map((target) => target.repository_id),
    );
    const target = source.run.target;
    expect(target?.kind).toBe("fleet_plan");
    if (!target || target.kind !== "fleet_plan") {
      throw new Error("default fixture Run must carry the canonical Fleet Plan target");
    }
    expect(source.repository_admissions.every((item) => item.plan_id === target.plan.plan_id)).toBe(true);
    expect(source.rerun_supported).toBe(true);
  });

  it("preserves one dynamic Run identity through start, events and detail", async () => {
    const client = new BrowserFixtureApiClient();
    const runId = "fleet-11111111-2222-4333-8444-555555555555";
    const target = FIXTURE_PLANS.target("python-quality");

    const started = await client.startSharedPlanRun(
      runId,
      "python-quality",
      [FIXTURE_REPOSITORIES.payments.repository_id],
      target.plan.plan_id,
      FIXTURE_PLANS.document("python-quality").sha256,
      target.identity.digest,
      { [FIXTURE_REPOSITORIES.payments.repository_id]: `fixture-repository-instance:${FIXTURE_REPOSITORIES.payments.repository_id}` },
      { coverage_threshold: "85" },
    );
    expect(started.outcome).toBe("saved");
    if (started.outcome !== "saved") return;
    expect(started.value.fleet_run_id).toBe(runId);
    expect(started.value.targets.map((item) => item.repository_id)).toEqual([FIXTURE_REPOSITORIES.payments.repository_id]);

    await client.fleetRunEvents(runId, 0);
    await client.fleetRunEvents(runId, 1);
    const terminal = await client.fleetRunEvents(runId, 1);
    expect(terminal.run.fleet_run_id).toBe(runId);

    const detail = await client.fleetRunDetail(runId);
    expect(detail.run.fleet_run_id).toBe(runId);
    expect(detail.run.targets.map((item) => item.repository_id)).toEqual([FIXTURE_REPOSITORIES.payments.repository_id]);
  });

  it("pins blocker semantics to repository identity instead of selection position", async () => {
    const client = new BrowserFixtureApiClient("blocked");
    const revision = FIXTURE_PLANS.document("python-quality").sha256;
    const withBlocker = await client.previewSharedPlan("python-quality", revision, [
      FIXTURE_REPOSITORIES.payments.repository_id,
      FIXTURE_REPOSITORIES.web.repository_id,
    ]);
    expect(withBlocker.blocked_count).toBe(1);
    expect(withBlocker.teams.find((item) => item.state === "BLOCKED")?.repository.repository_id).toBe(
      FIXTURE_REPOSITORIES.payments.repository_id,
    );

    const afterRemoval = await client.previewSharedPlan("python-quality", revision, [FIXTURE_REPOSITORIES.web.repository_id]);
    expect(afterRemoval.blocked_count).toBe(0);
    expect(afterRemoval.ready_count).toBe(1);
  });

  it("projects one reviewed execution digest through lock preview, repository preview, and Run admission", async () => {
    const client = new BrowserFixtureApiClient();
    const planKey = "python-quality";
    const document = FIXTURE_PLANS.document(planKey);
    const target = FIXTURE_PLANS.target(planKey);
    const repositoryId = FIXTURE_REPOSITORIES.payments.repository_id;

    const lockPreview = await client.previewSharedPlanLock(planKey, document.sha256);
    const preview = await client.previewSharedPlan(planKey, document.sha256, [repositoryId]);

    expect(lockPreview.execution_digest).toBe(target.identity.digest);
    expect(preview.execution_digest).toBe(target.identity.digest);

    const stale = await client.startSharedPlanRun(
      "fleet-stale-shared-execution",
      planKey,
      [repositoryId],
      target.plan.plan_id,
      document.sha256,
      `sha256:${"0".repeat(64)}`,
      { [repositoryId]: `fixture-repository-instance:${repositoryId}` },
    );
    expect(stale.outcome).toBe("stale");
    await expect(client.fleetRunDetail("fleet-stale-shared-execution")).rejects.toThrow("Unknown fixture Run");
  });

  it("resolves scale failure detail from the registered Run record, not the Run ID format", async () => {
    const client = new BrowserFixtureApiClient("scale");
    const page = await client.fleetFailureRepositories("failure-group-scale-000", undefined, undefined, 0, 1);
    const item = (page.items ?? [])[0];
    expect(item).toBeDefined();
    if (!item) return;
    const detail = await client.fleetRunDetail(item.fleet_run_id);
    expect(detail.run.fleet_run_id).toBe(item.fleet_run_id);
    expect(detail.run.targets[0]?.repository_id).toBe(item.repository.repository_id);
  });

  it("fails closed for unknown fixture identities", async () => {
    const client = new BrowserFixtureApiClient();
    await expect(client.fleetRunDetail("unknown-run")).rejects.toThrow("Unknown fixture Run");
    await expect(client.previewSharedPlan("python-quality", FIXTURE_PLANS.document("python-quality").sha256, ["unknown-repository"])).rejects.toThrow("Unknown fixture repository id");
    await expect(client.deleteSchedule("unknown-schedule", { generation_id: "missing", record_version: 1 })).rejects.toThrow("Unknown fixture schedule");
  });

  it("fails stale Schedule writes against the authority the browser actually reviewed", async () => {
    const client = new BrowserFixtureApiClient();
    const catalog = await client.schedules();
    const definition = catalog.schedules?.[0]?.definition;
    expect(definition).toBeDefined();
    if (!definition) return;

    const replaced = await client.updateSchedule(definition.schedule_id, {
      generation_id: definition.generation_id,
      record_version: definition.record_version,
      name: `${definition.name} replacement`,
      repository_ids: definition.repository_ids,
      repository_instance_ids: definition.repository_instance_ids,
      target: definition.target,
      interval_minutes: definition.interval_minutes,
      state: definition.state,
    });
    expect(replaced.outcome).toBe("saved");

    const staleDelete = await client.deleteSchedule(definition.schedule_id, {
      generation_id: definition.generation_id,
      record_version: definition.record_version,
    });
    expect(staleDelete.outcome).toBe("stale");
  });

  it("materializes the exact advisory-filtered dependency population identity", async () => {
    const client = new BrowserFixtureApiClient();
    const page = await client.dependencyRepositories("mlflow", undefined, undefined, "all", "all", 0, 20, "<2.18.0", "CVE-2026-MLFLOW");
    const result = await client.materializeDependencyTargets({
      expected_population_id: page.population_id,
      filter: { package_name: "mlflow", coverage: "matching", relationship: "all", evidence: "all", version_specifier: "<2.18.0", advisory_id: "CVE-2026-MLFLOW" },
    });
    expect(result.outcome).toBe("saved");
    if (result.outcome !== "saved") return;
    expect(result.value.query_id).toBe(page.population_id);
    await expect(client.dependencyTargetSelection(page.population_id)).resolves.toEqual(result.value);
  });

  it("fails stale dependency materialization when the displayed population identity changed", async () => {
    const client = new BrowserFixtureApiClient();
    const page = await client.dependencyRepositories("mlflow");
    const result = await client.materializeDependencyTargets({
      expected_population_id: `${page.population_id}-stale`,
      filter: { package_name: "mlflow", relationship: "all", evidence: "all" },
    });
    expect(result.outcome).toBe("stale");
  });

  it("fails stale Team execution before registering a Run", async () => {
    const client = new BrowserFixtureApiClient();
    const stale = await client.startTeamRun(
      "fleet-stale-team",
      FIXTURE_REPOSITORIES.payments.path,
      `${FIXTURE_SHA.slice(0, 63)}0`,
      `fixture-repository-instance:${FIXTURE_REPOSITORIES.payments.repository_id}`,
    );
    expect(stale.outcome).toBe("stale");
    await expect(client.fleetRunDetail("fleet-stale-team")).rejects.toThrow("Unknown fixture Run");
  });

  it("fails stale Team repository instance before registering a Run", async () => {
    const client = new BrowserFixtureApiClient();
    const stale = await client.startTeamRun(
      "fleet-stale-team-instance",
      FIXTURE_REPOSITORIES.payments.path,
      FIXTURE_SHA,
      "fixture-repository-instance:replacement",
    );
    expect(stale.outcome).toBe("stale");
    await expect(client.fleetRunDetail("fleet-stale-team-instance")).rejects.toThrow("Unknown fixture Run");
  });

  it("fails stale Shared Plan repository instance before registering a Run", async () => {
    const client = new BrowserFixtureApiClient();
    const target = FIXTURE_PLANS.target("python-quality");
    const repositoryId = FIXTURE_REPOSITORIES.payments.repository_id;
    const stale = await client.startSharedPlanRun(
      "fleet-stale-shared-instance",
      "python-quality",
      [repositoryId],
      target.plan.plan_id,
      FIXTURE_PLANS.document("python-quality").sha256,
      target.identity.digest,
      { [repositoryId]: "fixture-repository-instance:replacement" },
    );
    expect(stale.outcome).toBe("stale");
    await expect(client.fleetRunDetail("fleet-stale-shared-instance")).rejects.toThrow("Unknown fixture Run");
  });

  it("reruns from registered Run authority instead of client-supplied target data", async () => {
    const client = new BrowserFixtureApiClient();
    const source = await client.fleetRunDetail(DEFAULT_RUN_ID);
    const result = await client.rerunFleetRun(DEFAULT_RUN_ID, "run-fixture-rerun");
    expect(result.outcome).toBe("saved");
    if (result.outcome !== "saved") return;
    expect(result.value.fleet_run_id).toBe("run-fixture-rerun");
    expect(result.value.target).toEqual(source.run.target);
    expect(result.value.targets).toEqual(source.run.targets);
  });

  it("keeps scale Run event causality newer than the durable scale snapshot", async () => {
    const client = new BrowserFixtureApiClient("scale");
    const overview = await client.fleetRunOverview();
    const fleet = await client.fleet();
    const repository = fleet.repositories?.[0]?.repository;
    expect(repository).toBeDefined();
    if (!repository) return;
    const target = FIXTURE_PLANS.target("python-quality");
    const runId = "fleet-scale-causality-regression";
    const started = await client.startSharedPlanRun(
      runId,
      "python-quality",
      [repository.repository_id],
      target.plan.plan_id,
      FIXTURE_PLANS.document("python-quality").sha256,
      target.identity.digest,
      { [repository.repository_id]: `fixture-repository-instance:${repository.repository_id}` },
    );
    expect(started.outcome).toBe("saved");

    const first = await client.fleetRunEvents(runId, 0);
    await client.fleetRunEvents(runId, first.next_sequence);
    const terminal = await client.fleetRunEvents(runId, first.next_sequence);

    expect(first.journal_revision).toBeGreaterThan(overview.journal_revision);
    expect(terminal.journal_revision).toBeGreaterThan(first.journal_revision);
    expect(terminal.run.status).not.toBe("RUNNING");
  });

});
