import { FleetRunStatus, GameTapeStatus } from "@generated/enums";
import type {
  CatalogCandidate,
  CatalogExploreRequest,
  CatalogExploration,
  CatalogPreview,
  CatalogPreviewRequest,
  CatalogRunRequest,
  FleetPlanTarget,
  FleetRunDetail,
  FleetRunState,
  RepositoryIdentity,
  RepositorySourceAuthority,
  RunArtifactExport,
  RunArtifactExportRequest,
} from "@generated/models";

import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";
import type { FixtureRunRegistry } from "./FixtureRunRegistry";
import { FIXTURE_REPOSITORY_LIST, fixtureRepositoryInstanceId } from "./identities";
import { FIXTURE_FLEET_PLAN_TARGET } from "./plans";
import { runState } from "./runProjection";
import { CATALOG_EXPLORE_RELEASE_EVENT, CATALOG_REQUEST_EVENT, waitForFixtureEvent } from "./scenarios";

export const CATALOG_FIXTURE_CANDIDATE: CatalogCandidate = {
  name: "order-gateway",
  description: "Synthetic order gateway for a local code-discovery demonstration.",
  owner: "group:default/platform-team",
  type: "service",
  lifecycle: "experimental",
  languages: ["python", "typescript"],
};

export const CATALOG_FIXTURE_YAML = "apiVersion: backstage.io/v1alpha1\nkind: Component\nmetadata:\n  name: \"order-gateway\"\n  description: \"Synthetic order gateway for a local code-discovery demonstration.\"\n  tags:\n  - \"python\"\n  - \"typescript\"\nspec:\n  type: \"service\"\n  lifecycle: \"experimental\"\n  owner: \"group:default/platform-team\"\n";
export const CATALOG_FIXTURE_STEP_ID = "catalog-generate.catalog";
export const CATALOG_FIXTURE_ARTIFACT_SHA = "fae615ec7411540de633a73036553d040960bfc3790c9ef0a8246c13f5da3836";
export const CATALOG_FIXTURE_REFERENCE = `ref-${"c".repeat(32)}`;
export const CATALOG_FIXTURE_PLAN_ID = "plan:reviewed-catalog-browser-fixture";
export const CATALOG_FIXTURE_PLAN_SHA = "e".repeat(64);
export const CATALOG_FIXTURE_EXECUTION_DIGEST = `sha256:${"f".repeat(64)}`;

export const CATALOG_FIXTURE_TARGET: FleetPlanTarget = {
  ...FIXTURE_FLEET_PLAN_TARGET,
  source: { kind: "plan_file", sha256: CATALOG_FIXTURE_PLAN_SHA },
  identity: { digest: CATALOG_FIXTURE_EXECUTION_DIGEST },
  plan: {
    ...FIXTURE_FLEET_PLAN_TARGET.plan,
    plan_id: CATALOG_FIXTURE_PLAN_ID,
    name: "Reviewed catalog generation",
    description: "Synthetic source-bound catalog browser fixture.",
    execution: { purpose: "verification" },
    requirements: [],
    imports: [],
    steps: [{ kind: "command", step_id: CATALOG_FIXTURE_STEP_ID, argv: ["python3", "generate_catalog.py"] }],
    step_sources: [],
    configuration: { schema_name: "oh-goon.plan-configuration/v1", root_source_sha256: CATALOG_FIXTURE_PLAN_SHA, entries: [], omitted_entries: 0 },
  },
};

export function catalogFixtureSource(repository: RepositoryIdentity): RepositorySourceAuthority {
  const position = FIXTURE_REPOSITORY_LIST.findIndex((item) => item.repository_id === repository.repository_id);
  if (position < 0) throw new Error("Unknown catalog fixture repository");
  const digest = String(position + 1).repeat(64);
  return {
    subject_instance_id: `repo-subject-instance-${digest}`,
    source_snapshot: { sha256: digest, byte_count: 1225, entry_count: 9 },
    authority_id: `repository-source-authority-${digest}`,
  };
}

export function catalogFixtureExploration(repository: RepositoryIdentity, path: string | null = "src/service.py"): CatalogExploration {
  const selectedFile = path === "src/service.py";
  return {
    repository,
    repository_instance_id: fixtureRepositoryInstanceId(repository.repository_id),
    source_authority: catalogFixtureSource(repository),
    producer_identity: "hashmarks:synthetic-browser-evidence",
    candidate_options: { component_types: ["service", "website", "library"], lifecycles: ["experimental", "production", "deprecated"] },
    languages: [{ name: "python", count: 3 }, { name: "typescript", count: 1 }],
    hits: [{ path: "src/service.py", kind: "function", name: "submit_order", qualname: "submit_order", signature: "def submit_order(order_id: str) -> dict[str, str]", first_line: 6, last_line: 8 }],
    relations: selectedFile ? [{ source_path: "src/service.py", target: "accepted_status", kind: "call", line: 8, confidence: "static-name", resolved_paths: ["src/order_status.py"] }] : [],
    relationship_status: selectedFile ? "observed" : path === null ? "select-file" : "unsupported",
    relationship_message: selectedFile ? "Producer-bounded, one-hop symbol call observations; unresolved calls remain unproven." : "Select a localized symbol hit in this file for bounded producer relationships; unavailable evidence does not prove absence.",
    relationship_qualification: selectedFile ? {
      max_depth: 1, call_limit_per_symbol: 8, ref_limit_per_symbol: 8,
      unresolved_reasons: [], external_or_unindexed_reasons: [],
      lifecycle_completeness: "complete-within-supported-scope", lifecycle_truncated: false,
    } : null,
    qualification: {
      generation: 1, identity_generation: null,
      freshness: "unknown", completeness: "complete", truncated: false, negative_evidence: "not-applicable",
      admissibility_reasons: ["repository-freshness-unknown"], repository_wide_completeness: "not-claimed",
    },
    git_author: selectedFile ? {
      status: "available", path: "src/service.py", revision: "b".repeat(40), commit: "b".repeat(40),
      author: repository.repository_id === "repo-catalog" ? "Demo Catalog Contributor" : "Demo Web Contributor",
      date: "2026-01-01T10:00:00Z", message: "Native Git last matching commit; not service ownership.",
    } : { status: path === null ? "select-file" : "unavailable", path, revision: null, commit: null, author: null, date: null, message: path === null ? "Select a source hit to inspect Git history." : "Native Git history is unavailable for the selected fixture member." },
    ownership_status: "manual-review-required", provider_mode: "none",
    technical_evidence: { fixture: "synthetic-browser-evidence", generation: 1 },
  };
}

export function catalogFixturePreview(repository: RepositoryIdentity): CatalogPreview {
  return {
    repository, source_authority: catalogFixtureSource(repository),
    candidate: structuredClone(CATALOG_FIXTURE_CANDIDATE), yaml: CATALOG_FIXTURE_YAML,
    plan_id: CATALOG_FIXTURE_PLAN_ID, plan_sha256: CATALOG_FIXTURE_PLAN_SHA,
    execution_digest: CATALOG_FIXTURE_EXECUTION_DIGEST, provider_mode: "none",
  };
}

/** One recorded generator Step, with its retained output on the original Run. */
export function catalogFixtureRunDetail(detail: FleetRunDetail): FleetRunDetail {
  const repository = detail.game_tape.repositories[0];
  const originalStep = repository?.steps?.[0];
  if (!repository || !originalStep || detail.game_tape.repositories.length !== 1) throw new Error("Catalog fixture requires one original repository/Step");
  const completed = detail.run.status === FleetRunStatus.COMPLETED;
  const step = {
    ...originalStep,
    step_execution_id: `${detail.run.fleet_run_id}:${repository.repository.repository_id}:${CATALOG_FIXTURE_STEP_ID}`,
    step_id: CATALOG_FIXTURE_STEP_ID, command: ["python3", "generate_catalog.py"],
    status: completed ? GameTapeStatus.PASSED : GameTapeStatus.RUNNING,
    duration_ms: completed ? 102000 : null,
    finished_at: completed ? repository.finished_at : null,
    exit_code: completed ? 0 : null,
    why: { declaration: "local:goons/catalog/generate/goon.yml:4", source_location: { source: "local:goons/catalog/generate/goon.yml", line: 4, column: 3 }, reached: true, explanation: "Original admitted catalog generation Step." },
    artifacts: completed ? [{
      status: "PRESENT" as const, path: "catalog-info.yaml", sha256: CATALOG_FIXTURE_ARTIFACT_SHA, byte_count: 296,
      retained_object: { kind: "file" as const, sha256: "c".repeat(64), byte_count: 296, entry_count: 1, object_id: `file-sha256-${"c".repeat(64)}`, reference_id: CATALOG_FIXTURE_REFERENCE, owner_repository_id: repository.repository.repository_id },
    }] : [],
    settlement: completed ? { owner: "fleet_step_settlement" as const, outcome: "SUCCESS" as const, cleanup: "VERIFIED" as const, reason: "ACCEPTED_EXIT" as const, observed_returncode: 0 } : originalStep.settlement,
  };
  repository.steps = [step];
  repository.bottleneck_step_id = CATALOG_FIXTURE_STEP_ID;
  repository.bottleneck_percent = 100;
  detail.game_tape.summary = { ...detail.game_tape.summary, planned_steps: 1, executed_steps: 1, passed_steps: completed ? 1 : 0, failed_steps: 0, not_run_steps: 0 };
  detail.repository_admissions = detail.repository_admissions.map((admission) => ({ ...admission, source_authority: catalogFixtureSource(repository.repository), active_step_ids: [CATALOG_FIXTURE_STEP_ID] }));
  return detail;
}

interface CatalogFixtureDependencies {
  repositoryForId(repositoryId: string): RepositoryIdentity;
  runs: FixtureRunRegistry;
  saved<Value>(value: Value): WriteCommandResult<Value>;
  holdFirstExploration: boolean;
}

/** Scripted public response envelopes; original fixture owners retain Run truth. */
export class CatalogFixtureApi {
  private readonly previews = new Map<string, CatalogPreview>();
  private readonly destinations = new Set<string>();
  private explorationHeld = false;

  constructor(private readonly dependencies: CatalogFixtureDependencies) {}

  async explore(repositoryId: string, request: CatalogExploreRequest): Promise<CatalogExploration> {
    this.record("explore", repositoryId, request);
    const repository = this.dependencies.repositoryForId(repositoryId);
    if (request.expected_repository_instance_id !== fixtureRepositoryInstanceId(repositoryId)) throw new Error("Catalog fixture repository instance is stale");
    if (this.dependencies.holdFirstExploration && !this.explorationHeld) {
      this.explorationHeld = true;
      await waitForFixtureEvent(CATALOG_EXPLORE_RELEASE_EVENT);
    }
    return catalogFixtureExploration(repository, request.path ?? null);
  }

  async preview(repositoryId: string, request: CatalogPreviewRequest): Promise<CatalogPreview> {
    this.record("preview", repositoryId, request);
    const repository = this.dependencies.repositoryForId(repositoryId);
    if (canonicalIdentity(request.source_authority) !== canonicalIdentity(catalogFixtureSource(repository))) throw new Error("Catalog fixture source is stale");
    if (canonicalIdentity(request.candidate) !== canonicalIdentity(CATALOG_FIXTURE_CANDIDATE)) throw new Error("Catalog browser fixture expects its supplied synthetic candidate");
    const preview = catalogFixturePreview(repository);
    this.previews.set(repositoryId, preview);
    return structuredClone(preview);
  }

  async start(repositoryId: string, request: CatalogRunRequest): Promise<WriteCommandResult<FleetRunState>> {
    this.record("run", repositoryId, request);
    const repository = this.dependencies.repositoryForId(repositoryId);
    const preview = this.previews.get(repositoryId);
    if (!preview
      || request.expected_plan_id !== preview.plan_id
      || request.expected_plan_sha256 !== preview.plan_sha256
      || request.expected_execution_digest !== preview.execution_digest
      || canonicalIdentity(request.source_authority) !== canonicalIdentity(preview.source_authority)
      || canonicalIdentity(request.candidate) !== canonicalIdentity(preview.candidate)) {
      return { outcome: "stale", diagnosticId: "fixture:catalog-review-changed" };
    }
    this.dependencies.runs.register({ runId: request.fleet_run_id, targets: [repository], planTarget: CATALOG_FIXTURE_TARGET, terminalAfterEventReads: 3 });
    return this.dependencies.saved(runState(request.fleet_run_id, FleetRunStatus.RUNNING, [repository], 1, 0, CATALOG_FIXTURE_TARGET));
  }

  async exportArtifact(runId: string, request: RunArtifactExportRequest): Promise<WriteCommandResult<RunArtifactExport>> {
    this.record("export", request.repository_id, { runId, ...request });
    const record = this.dependencies.runs.require(runId);
    if (record.planTarget.plan.plan_id !== CATALOG_FIXTURE_PLAN_ID
      || !this.dependencies.runs.isTerminal(record)
      || record.targets[0]?.repository_id !== request.repository_id
      || request.step_id !== CATALOG_FIXTURE_STEP_ID
      || request.path !== "catalog-info.yaml") {
      return { outcome: "stale", diagnosticId: "fixture:catalog-artifact-owner" };
    }
    if (this.destinations.has(request.destination)) return { outcome: "write-error", diagnosticId: "fixture:catalog-export-conflict", recovery: "blocked" };
    this.destinations.add(request.destination);
    return this.dependencies.saved({
      status: "exported", fleet_run_id: runId, repository_id: request.repository_id,
      step_id: request.step_id, artifact_path: request.path, destination: request.destination,
      sha256: CATALOG_FIXTURE_ARTIFACT_SHA, byte_count: 296,
      reference_id: CATALOG_FIXTURE_REFERENCE, cleanup_status: "verified", cleanup_failure: null,
    });
  }

  private record(kind: "explore" | "preview" | "run" | "export", repositoryId: string, request: unknown): void {
    if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(CATALOG_REQUEST_EVENT, { detail: { kind, repositoryId, request: structuredClone(request) } }));
  }
}
