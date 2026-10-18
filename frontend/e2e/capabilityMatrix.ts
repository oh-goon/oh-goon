export interface BrowserCapability {
  page: "overview" | "repositories" | "dependencies" | "plans" | "shared-runs" | "schedules" | "runs" | "scout" | "settings" | "help" | "recovery" | "tools";
  heading: string;
  standard: boolean;
  criticalInformation: readonly string[];
}

export const browserCapabilities: readonly BrowserCapability[] = [
  { page: "overview", heading: "Overview", standard: true, criticalInformation: ["Successful", "Failed", "Running", "Queued"] },
  { page: "repositories", heading: "Repositories", standard: true, criticalInformation: ["Repository", "readiness", "Run CI/CD"] },
  { page: "dependencies", heading: "Dependencies", standard: true, criticalInformation: ["dependency search", "evidence coverage", "repository population", "Use all"] },
  { page: "plans", heading: "Plan Builder", standard: true, criticalInformation: ["Saved Plan", "Plan key", "plan.yml content"] },
  { page: "shared-runs", heading: "New shared run", standard: true, criticalInformation: ["Saved Plan", "Repositories", "Run"] },
  { page: "schedules", heading: "Schedules", standard: true, criticalInformation: ["repository", "Plan", "frequency", "latest run"] },
  { page: "runs", heading: "Runs", standard: true, criticalInformation: ["outcome", "Overview", "Timeline", "Logs"] },
  { page: "scout", heading: "The Scout", standard: true, criticalInformation: ["Repository activity", "Needs Scouting", "Heavy Hitters", "Goon Leaders"] },
  { page: "settings", heading: "Settings", standard: false, criticalInformation: ["interface mode", "locations", "browser preferences"] },
  { page: "help", heading: "Using oh-goon", standard: false, criticalInformation: ["Repositories", "New shared run", "Schedules", "Runs"] },
  { page: "recovery", heading: "Recovery", standard: false, criticalInformation: ["quarantine", "safe recovery action"] },
  { page: "tools", heading: "Tools", standard: false, criticalInformation: ["advanced operator tool"] },
] as const;
