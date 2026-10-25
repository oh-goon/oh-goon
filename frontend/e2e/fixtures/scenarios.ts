export const SHARED_RUN_SUBMIT_RELEASE_EVENT = "oh-goon:e2e:release-shared-run-submit";

export interface BrowserFixtureScenarioDefinition {
  readonly blockedRepositoryRole?: "payments";
  readonly holdSubmit?: boolean;
  readonly submitFailureDiagnosticId?: string;
  readonly terminalOutcome?: "failed";
  readonly campaignDecision?: "converged" | "missing" | "not_admitted";
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
