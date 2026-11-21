import { FleetRepositoryScope } from "@generated/enums";
import type { ScheduleReplace, SharedPlanScheduleCreateRequest } from "@generated/models";
import { decodeResponse, operations } from "@generated/operations";

import { BrowserFixtureApiClient } from "../BrowserFixtureApiClient";
import { FIXTURE_REPOSITORIES, FIXTURE_REPOSITORY_LIST, fixtureRepositoryInstanceId } from "./identities";
import { FIXTURE_PLANS, FIXTURE_SHA } from "./plans";
import { DEFAULT_RUN_ID } from "./runIds";
import { CATALOG_FIXTURE_CANDIDATE, CATALOG_FIXTURE_STEP_ID } from "./catalog";
import { CONTINUATION_CAMPAIGN_ID } from "./agentContinuation";

function reviewedScheduleRequest(): SharedPlanScheduleCreateRequest {
  const repositoryId = FIXTURE_REPOSITORIES.payments.repository_id;
  const target = FIXTURE_PLANS.target("python-quality");
  return {
    name: "Reviewed quality",
    repository_ids: [repositoryId],
    expected_repository_instance_ids: { [repositoryId]: fixtureRepositoryInstanceId(repositoryId) },
    plan_key: "python-quality",
    expected_plan_revision: FIXTURE_PLANS.document("python-quality").sha256,
    expected_plan_id: target.plan.plan_id,
    expected_execution_digest: target.identity.digest,
    interval_minutes: 15,
  };
}

describe("BrowserFixtureApiClient canonical fixture contracts", () => {
  it("serves typed current continuation evidence and records its receipt on a separate Run", async () => {
    const client = new BrowserFixtureApiClient();
    const inspection = decodeResponse(operations.inspect_agent_continuation.operationId, await client.inspectAgentContinuation(CONTINUATION_CAMPAIGN_ID));
    const saved = await client.continueAgentCampaign(CONTINUATION_CAMPAIGN_ID, { new_fleet_run_id: "fresh-contract-run", review: inspection.review, feedback: "Clarify the original regression.", authorize: true });
    expect(saved.outcome).toBe("saved");
    if (saved.outcome !== "saved") return;
    const result = decodeResponse(operations.continue_agent_campaign.operationId, saved.value);
    const detail = decodeResponse(operations.get_fleet_run.operationId, await client.fleetRunDetail(result.fleet_run_id));
    expect(detail.run.fleet_run_id).not.toBe(inspection.previous_run_id);
    expect(detail.run.target?.kind === "fleet_plan" && detail.run.target.agent_continuation).toEqual(result.receipt);
    expect((await client.fleetRunDetail(inspection.previous_run_id)).run.status).toBe("FAILED");
  });
  it("refuses a stale template digest before reporting a saved Plan", async () => {
    const client = new BrowserFixtureApiClient();
    const template = (await client.planTemplates()).templates?.[0];
    expect(template).toBeDefined();
    if (!template) return;
    const control = await new BrowserFixtureApiClient().createSharedPlanFromTemplate(template.template_key, template.sha256);
    expect(control.outcome).toBe("saved");
    if (control.outcome !== "saved") return;
    decodeResponse(operations.create_shared_plan_from_template.operationId, control.value);
    await expect(client.createSharedPlanFromTemplate(template.template_key, "0".repeat(64))).resolves.toMatchObject({ outcome: "stale" });
    const accepted = await client.createSharedPlanFromTemplate(template.template_key, template.sha256);
    expect(accepted.outcome).toBe("saved");
    if (accepted.outcome !== "saved") return;
    decodeResponse(operations.create_shared_plan_from_template.operationId, accepted.value);
    expect(accepted).toEqual(control);
  });

  it("creates a Schedule from the exact reviewed Plan and repository authority", async () => {
    const client = new BrowserFixtureApiClient();
    const request = reviewedScheduleRequest();
    const accepted = await client.createScheduleFromSharedPlan("reviewed-quality", request);
    expect(accepted.outcome).toBe("saved");
    if (accepted.outcome !== "saved") return;
    const definition = decodeResponse(operations.create_schedule_from_shared_plan.operationId, accepted.value);
    expect(definition.repository_instance_ids).toEqual(request.expected_repository_instance_ids);
    expect(definition.target.plan.plan_id).toBe(request.expected_plan_id);
    expect(definition.target.identity.digest).toBe(request.expected_execution_digest);
  });

  it("keeps one Schedule when concurrent creates conflict on the same identity", async () => {
    const client = new BrowserFixtureApiClient();
    const request = reviewedScheduleRequest();
    const results = await Promise.all([
      client.createScheduleFromSharedPlan("concurrent-quality", request),
      client.createScheduleFromSharedPlan("concurrent-quality", { ...request, name: "Different quality" }),
    ]);
    expect(results.map((result) => result.outcome).sort()).toEqual(["saved", "stale"]);
    const saved = results.find((result) => result.outcome === "saved");
    expect(saved?.outcome).toBe("saved");
    if (saved?.outcome !== "saved") return;
    const rows = (await client.schedules()).schedules?.filter((item) => item.definition.schedule_id === "concurrent-quality");
    expect(rows).toHaveLength(1);
    expect(rows?.[0]?.definition).toEqual(saved.value);
  });

  it("reuses an identical concurrent Schedule create without duplicating or resetting its row", async () => {
    const client = new BrowserFixtureApiClient();
    const request = reviewedScheduleRequest();
    const before = await client.schedules();
    const results = await Promise.all([
      client.createScheduleFromSharedPlan("idempotent-quality", request),
      client.createScheduleFromSharedPlan("idempotent-quality", request),
    ]);
    expect(results.every((result) => result.outcome === "saved")).toBe(true);
    const [first, second] = results;
    if (first?.outcome !== "saved" || second?.outcome !== "saved") return;
    expect(second.value).toEqual(first.value);
    const current = await client.schedules();
    expect(current.schedules?.filter((item) => item.definition.schedule_id === "idempotent-quality")).toHaveLength(1);
    expect(current.schedules?.filter((item) => item.definition.schedule_id !== "idempotent-quality")).toEqual(before.schedules);
    await expect(client.createScheduleFromSharedPlan("idempotent-quality", request)).resolves.toMatchObject({ outcome: "saved", value: first.value });
    expect(await client.schedules()).toEqual(current);
  });

  it.each(["replace", "delete"] as const)("refuses predecessor %s tokens after recreating a Schedule", async (operation) => {
    const client = new BrowserFixtureApiClient();
    const request = reviewedScheduleRequest();
    const created = await client.createScheduleFromSharedPlan("recreated-quality", request);
    expect(created.outcome).toBe("saved");
    if (created.outcome !== "saved") return;
    const predecessor = created.value;
    const oldToken = { generation_id: predecessor.generation_id, record_version: predecessor.record_version };
    await expect(client.deleteSchedule(predecessor.schedule_id, oldToken)).resolves.toMatchObject({ outcome: "saved" });
    const recreated = await client.createScheduleFromSharedPlan(predecessor.schedule_id, request);
    expect(recreated.outcome).toBe("saved");
    if (recreated.outcome !== "saved") return;
    expect(recreated.value.record_version).toBe(predecessor.record_version);
    const current = await client.schedules();
    const replace: ScheduleReplace = {
      ...oldToken,
      name: "Old generation replacement",
      repository_ids: predecessor.repository_ids,
      repository_instance_ids: predecessor.repository_instance_ids,
      target: predecessor.target,
      interval_minutes: predecessor.interval_minutes,
      state: predecessor.state,
    };
    const stale = operation === "replace"
      ? await client.updateSchedule(predecessor.schedule_id, replace)
      : await client.deleteSchedule(predecessor.schedule_id, oldToken);
    expect(stale.outcome).toBe("stale");
    expect(await client.schedules()).toEqual(current);
    expect(recreated.value.generation_id).not.toBe(predecessor.generation_id);
    expect(recreated.value.record_version).toBe(0);
    const updated = await client.updateSchedule(predecessor.schedule_id, {
      ...replace, generation_id: recreated.value.generation_id, record_version: recreated.value.record_version,
    });
    expect(updated.outcome).toBe("saved");
    if (updated.outcome !== "saved") return;
    await expect(client.deleteSchedule(predecessor.schedule_id, {
      generation_id: updated.value.generation_id, record_version: updated.value.record_version,
    })).resolves.toMatchObject({ outcome: "saved" });
    expect((await client.schedules()).schedules?.some((item) => item.definition.schedule_id === predecessor.schedule_id)).toBe(false);
  });

  it.each([
    ["revision", "stale"],
    ["plan-id", "write-error"],
    ["execution-digest", "write-error"],
    ["repository-instance", "stale"],
    ["missing-instance", "write-error"],
    ["extra-instance", "write-error"],
    ["duplicate-repositories", "write-error"],
    ["empty-repositories", "write-error"],
  ] as const)("refuses Schedule creation with invalid %s without persisting it", async (field, outcome) => {
    const client = new BrowserFixtureApiClient();
    const request = reviewedScheduleRequest();
    const repositoryId = request.repository_ids[0]!;
    if (field === "revision") request.expected_plan_revision = "0".repeat(64);
    if (field === "plan-id") request.expected_plan_id = "unreviewed-plan";
    if (field === "execution-digest") request.expected_execution_digest = `sha256:${"0".repeat(64)}`;
    if (field === "repository-instance") request.expected_repository_instance_ids = { [repositoryId]: "replacement-instance" };
    if (field === "missing-instance") request.expected_repository_instance_ids = {};
    if (field === "extra-instance") request.expected_repository_instance_ids.extra = "extra-instance";
    if (field === "duplicate-repositories") request.repository_ids = [repositoryId, repositoryId];
    if (field === "empty-repositories") request.repository_ids = [];
    const before = await client.schedules();
    await expect(client.createScheduleFromSharedPlan("unreviewed-quality", request)).resolves.toMatchObject({ outcome });
    expect(await client.schedules()).toEqual(before);
  });

  it.each(["repositories", "instances", "target"] as const)("refuses Schedule metadata update that replaces %s authority", async (field) => {
    const client = new BrowserFixtureApiClient();
    const before = await client.schedules();
    const definition = before.schedules?.[0]?.definition;
    expect(definition).toBeDefined();
    if (!definition) return;
    const request: ScheduleReplace = {
      generation_id: definition.generation_id,
      record_version: definition.record_version,
      name: `${definition.name} renamed`,
      repository_ids: [...definition.repository_ids],
      repository_instance_ids: { ...definition.repository_instance_ids },
      target: structuredClone(definition.target),
      interval_minutes: definition.interval_minutes,
      state: definition.state,
    };
    if (field === "repositories") {
      const repositoryId = FIXTURE_REPOSITORIES.web.repository_id;
      request.repository_ids = [repositoryId];
      request.repository_instance_ids = { [repositoryId]: fixtureRepositoryInstanceId(repositoryId) };
    }
    if (field === "instances") request.repository_instance_ids[request.repository_ids[0]!] = "replacement-instance";
    if (field === "target") request.target.identity.digest = `sha256:${"0".repeat(64)}`;
    await expect(client.updateSchedule(definition.schedule_id, request)).resolves.toMatchObject({ outcome: "write-error", recovery: "blocked" });
    expect(await client.schedules()).toEqual(before);
  });

  it("refuses duplicated Shared Run targets even when an extra instance key masks their count", async () => {
    const client = new BrowserFixtureApiClient();
    const repositoryId = FIXTURE_REPOSITORIES.payments.repository_id;
    const target = FIXTURE_PLANS.target("python-quality");
    const runId = "fleet-duplicate-targets";
    const result = await client.startSharedPlanRun(
      runId, "python-quality", [repositoryId, repositoryId], target.plan.plan_id,
      FIXTURE_PLANS.document("python-quality").sha256, target.identity.digest,
      { [repositoryId]: fixtureRepositoryInstanceId(repositoryId), extra: "extra-instance" },
    );
    expect(result).toMatchObject({ outcome: "write-error", recovery: "blocked" });
    await expect(client.fleetRunDetail(runId)).rejects.toThrow("Unknown fixture Run");
  });

  it("preserves catalog source/preview authority and the original retained producer through generated contracts", async () => {
    const client = new BrowserFixtureApiClient();
    const repositoryId = FIXTURE_REPOSITORIES.catalog.repository_id;
    const exploration = decodeResponse(operations.catalog_explore.operationId, await client.catalogExplore(repositoryId, { expected_repository_instance_id: fixtureRepositoryInstanceId(repositoryId), query: "submit_order" }));
    const candidate = { source_authority: exploration.source_authority, candidate: CATALOG_FIXTURE_CANDIDATE };
    const preview = decodeResponse(operations.catalog_preview.operationId, await client.catalogPreview(repositoryId, candidate));
    const request = { ...candidate, expected_plan_id: preview.plan_id, expected_plan_sha256: preview.plan_sha256, expected_execution_digest: preview.execution_digest, fleet_run_id: "fleet-catalog-browser-contract" };
    await expect(client.startCatalogRun(repositoryId, { ...request, expected_plan_sha256: "0".repeat(64) })).resolves.toMatchObject({ outcome: "stale" });
    const submitted = await client.startCatalogRun(repositoryId, request);
    expect(submitted.outcome).toBe("saved");
    if (submitted.outcome !== "saved") return;
    decodeResponse(operations.catalog_run.operationId, submitted.value);
    await client.fleetRunEvents(request.fleet_run_id, 0);
    await client.fleetRunEvents(request.fleet_run_id, 1);
    await client.fleetRunEvents(request.fleet_run_id, 1);
    const detail = decodeResponse(operations.get_fleet_run.operationId, await client.fleetRunDetail(request.fleet_run_id));
    expect(detail.run.fleet_run_id).toBe(request.fleet_run_id);
    expect(detail.game_tape.repositories[0]?.steps?.map((step) => step.step_id)).toEqual([CATALOG_FIXTURE_STEP_ID]);
    expect(detail.repository_admissions[0]?.source_authority).toEqual(exploration.source_authority);
    const selection = { repository_id: repositoryId, step_id: CATALOG_FIXTURE_STEP_ID, path: "catalog-info.yaml", destination: "/tmp/catalog-demo/catalog-info.yaml" };
    const exported = await client.exportRunArtifact(request.fleet_run_id, selection);
    expect(exported.outcome).toBe("saved");
    if (exported.outcome !== "saved") return;
    decodeResponse(operations.export_run_artifact.operationId, exported.value);
    await expect(client.exportRunArtifact(request.fleet_run_id, selection)).resolves.toMatchObject({ outcome: "write-error" });
  });

  it("observes selected artifact bytes only on request while retaining the recorded evidence", async () => {
    const client = new BrowserFixtureApiClient("artifact-observation");
    const recorded = decodeResponse(operations.get_fleet_run.operationId, await client.fleetRunDetail(DEFAULT_RUN_ID));
    expect(recorded.game_tape.artifact_observation).toBeNull();
    const observed = decodeResponse(operations.get_fleet_run.operationId, await client.fleetRunDetail(DEFAULT_RUN_ID, undefined, true));
    expect(observed.game_tape.artifact_observation?.observations.map((item) => item.status)).toEqual(["AVAILABLE", "UNAVAILABLE"]);
    expect(observed.game_tape.artifact_observation?.omitted_artifacts).toBe(2);
    expect(observed.game_tape.repositories).toEqual(recorded.game_tape.repositories);
    expect(observed.run).toEqual(recorded.run);
    const unobserved = await client.fleetRunDetail(DEFAULT_RUN_ID);
    expect(unobserved.game_tape.artifact_observation).toBeNull();
  });

  it("serves a campaign without fabricating a Run and validates its generated response contract", async () => {
    const client = new BrowserFixtureApiClient();
    const campaign = decodeResponse(operations.get_agent_campaign.operationId, await client.agentCampaign("campaign-browser-refused"));
    expect(campaign.runs).toEqual([]);
    expect(campaign.decision_history.map((record) => record.fact.kind)).toEqual(["intent", "decision"]);
    expect(campaign.unstarted_selection?.selected_step_ids).toEqual(["refused-review-step"]);
    await expect(client.agentCampaign("unknown-campaign")).rejects.toThrow("Unknown fixture campaign");
  });

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
    expect(source.game_tape.configuration).toEqual(target.plan.configuration);
    expect(source.game_tape.configuration?.root_source_sha256).toBe(target.source.sha256);
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
    const page = await client.dependencyRepositories({ packageName: "mlflow", ecosystem: "pypi" }, undefined, undefined, "all", 0, 20, "<2.18.0", "CVE-2026-MLFLOW");
    const result = await client.materializeDependencyTargets({
      expected_population_id: page.population_id,
      filter: { package_name: "mlflow", coverage: "matching", evidence: "all", version_specifier: "<2.18.0", advisory_id: "CVE-2026-MLFLOW" },
    });
    expect(result.outcome).toBe("saved");
    if (result.outcome !== "saved") return;
    expect(result.value.query_id).toBe(page.population_id);
    await expect(client.dependencyTargetSelection(page.population_id)).resolves.toEqual(result.value);
  });

  it("fails stale dependency materialization when the displayed population identity changed", async () => {
    const client = new BrowserFixtureApiClient();
    const page = await client.dependencyRepositories({ packageName: "mlflow" });
    const result = await client.materializeDependencyTargets({
      expected_population_id: `${page.population_id}-stale`,
      filter: { package_name: "mlflow", evidence: "all" },
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
