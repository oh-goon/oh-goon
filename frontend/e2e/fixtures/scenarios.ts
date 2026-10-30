export const SHARED_RUN_SUBMIT_RELEASE_EVENT = "oh-goon:e2e:release-shared-run-submit";
export const ARTIFACT_OBSERVATION_REQUEST_EVENT = "oh-goon:e2e:artifact-observation-request";
export const ARTIFACT_OBSERVATION_RELEASE_EVENT = "oh-goon:e2e:release-artifact-observation";
export const ARTIFACT_OBSERVATION_RESPONSE_EVENT = "oh-goon:e2e:artifact-observation-response";

export interface BrowserFixtureScenarioDefinition {
  readonly blockedRepositoryRole?: "payments";
  readonly holdSubmit?: boolean;
  readonly submitFailureDiagnosticId?: string;
  readonly terminalOutcome?: "failed";
  readonly campaignDecision?: "converged" | "missing" | "not_admitted";
  readonly artifactObservation?: "immediate" | "held";
}

export const BROWSER_FIXTURE_SCENARIOS = {
  default: {},
  empty: {},
  scale: {},
  blocked: { blockedRepositoryRole: "payments" },
  "slow-submit": { holdSubmit: true },
  "write-error-submit": { submitFailureDiagnosticId: "fixture-shared-run-rejected" },
  "failed-run": { terminalOutcome: "failed" },
  "agent-campaign": { campaignDecision: "converged" },
  "agent-campaign-missing": { campaignDecision: "missing" },
  "agent-campaign-not-admitted": { campaignDecision: "not_admitted" },
  "artifact-observation": { artifactObservation: "immediate" },
  "artifact-observation-held": { artifactObservation: "held" },
} satisfies Record<string, BrowserFixtureScenarioDefinition>;

export type BrowserFixtureScenario = keyof typeof BROWSER_FIXTURE_SCENARIOS;

export function browserFixtureScenario(value: string | null): BrowserFixtureScenario {
  return value !== null && Object.hasOwn(BROWSER_FIXTURE_SCENARIOS, value)
    ? value as BrowserFixtureScenario
    : "default";
}

export function scenarioDefinition(scenario: BrowserFixtureScenario): BrowserFixtureScenarioDefinition {
  return BROWSER_FIXTURE_SCENARIOS[scenario];
}
