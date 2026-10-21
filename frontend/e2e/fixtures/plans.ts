import { LONG_PLAN_NAME } from "./identities";
import { DEFAULT_RUN_ID } from "./runIds";
import type {
  FleetPlanTarget,
  PlanTemplateCatalog,
  ResolvedFleetPlan,
  SharedPlanCatalog,
  SharedPlanDocument,
  SharedPlanSource,
} from "@generated/models";
import { FleetRunStatus, PlanInputType, PlanValueNamespace } from "@generated/enums";

export const FIXTURE_SHA = "a".repeat(64);
export const FIXTURE_PLAN_ID = "plan:python-production-quality-security:fixture";

const BASE_RESOLVED_PLAN: ResolvedFleetPlan = {
  plan_id: FIXTURE_PLAN_ID,
  name: LONG_PLAN_NAME,
  description: "Canonical browser-fixture Plan used to exercise current Fleet Run contracts.",
  execution: { purpose: "cicd" },
  requirements: [],
  imports: [],
  steps: [{ kind: "command", step_id: "browser-fixture-contract-step", argv: ["uv", "run", "pytest"] }],
  step_sources: [],
};

interface FixturePlanRecord {
  readonly planKey: string;
  readonly sha256: string;
  readonly byteCount: number;
  readonly sharedPlanId: string;
  readonly name: string;
  readonly content: string;
  readonly inputs: NonNullable<SharedPlanDocument["inputs"]>;
  readonly changedSinceLastRun: boolean;
  readonly lastRun: NonNullable<NonNullable<SharedPlanCatalog["plans"]>[number]["last_run"]> | null;
}

const PLAN_RECORDS: readonly FixturePlanRecord[] = [
  {
    planKey: "python-quality",
    sha256: "d".repeat(64),
    byteCount: 128,
    sharedPlanId: "shared-plan:python-quality:fixture",
    name: "python-quality",
    content: "version: 1\ninputs:\n- coverage_threshold\ngoons:\n- python/quality\n",
    inputs: [{ name: "coverage_threshold", input_type: "integer", default: 70, required: false, description: "Minimum test coverage percentage.", choices: [], minimum: 70, maximum: 100, non_empty: false, pattern: null }],
    changedSinceLastRun: false,
    lastRun: null,
  },
  {
    planKey: "python-production-quality-security",
    sha256: FIXTURE_SHA,
    byteCount: 4096,
    sharedPlanId: FIXTURE_PLAN_ID,
    name: LONG_PLAN_NAME,
    content: "version: 1\ngoons:\n- python/quality\n",
    inputs: [],
    changedSinceLastRun: true,
    lastRun: { fleet_run_id: DEFAULT_RUN_ID, accepted_at: "2026-08-21T16:00:00Z", status: FleetRunStatus.COMPLETED, team_count: 2, source_sha256: FIXTURE_SHA },
  },
  {
    planKey: "python-fast-check",
    sha256: "b".repeat(64),
    byteCount: 2048,
    sharedPlanId: "plan:python-fast-check:fixture",
    name: "Python fast quality check",
    content: "version: 1\ngoons:\n- python/quality\n",
    inputs: [],
    changedSinceLastRun: false,
    lastRun: null,
  },
  {
    planKey: "python-dependency-inventory",
    sha256: "c".repeat(64),
    byteCount: 1536,
    sharedPlanId: "plan:python-dependency-inventory:fixture",
    name: "Python dependency inventory",
    content: "version: 1\ngoons:\n- python/dependency-inventory\n",
    inputs: [],
    changedSinceLastRun: false,
    lastRun: null,
  },
];

const TEMPLATE_TO_PLAN = new Map<string, string>([
  ["python/quality", "python-quality"],
  ["python/dependency-inventory", "python-dependency-inventory"],
]);

export class FixturePlanRegistry {
  private readonly byKey = new Map(PLAN_RECORDS.map((record) => [record.planKey, record]));

  catalog(): SharedPlanCatalog {
    return { plans: PLAN_RECORDS.map((record) => this.summary(record)) };
  }

  templates(): PlanTemplateCatalog {
    return { templates: [
      { template_key: "python/quality", classification: "production", description: "Python quality: lint, typecheck, tests and coverage.", template_path: "templates/plans/python/quality", sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" },
      { template_key: "python/dependency-inventory", classification: "production", description: "Observe Python dependency evidence.", template_path: "templates/plans/python/dependency-inventory", sha256: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" },
    ] };
  }

  document(planKey: string): SharedPlanDocument {
    const record = this.require(planKey);
    return {
      ...this.summary(record),
      content: record.content,
      inputs: record.inputs.map((input) => ({ ...input })),
    };
  }

  target(planKey: string): FleetPlanTarget {
    const record = this.require(planKey);
    const source: SharedPlanSource = { kind: "shared_plan", plan_key: record.planKey, sha256: record.sha256 };
    return {
      kind: "fleet_plan",
      source,
      identity: { digest: `sha256:${record.sha256}` },
      plan: { ...BASE_RESOLVED_PLAN, plan_id: record.sharedPlanId, name: record.name,
        configuration: { schema_name: "oh-goon.plan-configuration/v1", root_source_sha256: record.sha256, entries: [{
          scope: null, namespace: PlanValueNamespace.INPUT, name: "project", input_type: PlanInputType.STRING,
          value_state: "defined", source_sha256: record.sha256, binding: { kind: "default" },
        }], omitted_entries: 0 },
      },
    };
  }

  planKeyForTemplate(templateKey: string): string {
    const planKey = TEMPLATE_TO_PLAN.get(templateKey);
    if (planKey === undefined) throw new Error(`Unknown Plan template ${templateKey}`);
    return planKey;
  }

  require(planKey: string): FixturePlanRecord {
    const record = this.byKey.get(planKey);
    if (record === undefined) throw new Error(`Unknown Shared Plan ${planKey}`);
    return record;
  }

  private summary(record: FixturePlanRecord): NonNullable<SharedPlanCatalog["plans"]>[number] {
    return {
      plan_key: record.planKey,
      sha256: record.sha256,
      byte_count: record.byteCount,
      shared_plan_id: record.sharedPlanId,
      changed_since_last_run: record.changedSinceLastRun,
      last_run: record.lastRun === null ? null : { ...record.lastRun },
    };
  }
}

export const FIXTURE_PLANS = new FixturePlanRegistry();
export const FIXTURE_RESOLVED_PLAN = BASE_RESOLVED_PLAN;
export const FIXTURE_FLEET_PLAN_TARGET = FIXTURE_PLANS.target("python-production-quality-security");
