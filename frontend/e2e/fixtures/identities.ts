import type { RepositoryIdentity } from "@generated/models";

export const LONG_REPOSITORY_NAME = "payments-api-with-a-deliberately-long-operational-repository-name-that-must-remain-readable-at-supported-browser-widths";
export const LONG_PLAN_NAME = "Python production quality and security verification with a deliberately long Plan name for realistic browser visibility testing";
export const LONG_GOON_NAME = "python-production-quality-security-composite-goon-with-a-deliberately-long-capability-name";
export const LONG_STEP_NAME = "validate-generated-contract-and-production-security-evidence-with-realistic-long-step-name";

export const FIXTURE_REPOSITORIES = {
  payments: repository("repo-payments", LONG_REPOSITORY_NAME, `/workspace/${LONG_REPOSITORY_NAME}`),
  web: repository("repo-web", "customer-portal-web", "/workspace/customer-portal-web"),
  worker: repository("repo-worker", "billing-background-worker", "/workspace/billing-background-worker"),
  search: repository("repo-search", "search-indexer", "/workspace/search-indexer"),
  catalog: repository("repo-catalog", "catalog-service", "/workspace/catalog-service"),
  notify: repository("repo-notify", "notification-service", "/workspace/notification-service"),
  report: repository("repo-report", "reporting-api", "/workspace/reporting-api"),
} as const satisfies Readonly<Record<string, RepositoryIdentity>>;

export const FIXTURE_REPOSITORY_LIST: readonly RepositoryIdentity[] = Object.values(FIXTURE_REPOSITORIES);
export const PRIMARY_REPOSITORY = FIXTURE_REPOSITORIES.payments;

export function fixtureRepositoryInstanceId(repositoryId: string): string {
  return `fixture-repository-instance:${repositoryId}`;
}

export class FixtureRepositoryRegistry {
  private readonly byId: ReadonlyMap<string, RepositoryIdentity>;
  private readonly byPath: ReadonlyMap<string, RepositoryIdentity>;

  constructor(readonly repositories: readonly RepositoryIdentity[]) {
    this.byId = uniqueMap(repositories, (item) => item.repository_id, "repository id");
    this.byPath = uniqueMap(repositories, (item) => item.path, "repository path");
  }

  requireById(repositoryId: string): RepositoryIdentity {
    const repository = this.byId.get(repositoryId);
    if (repository === undefined) throw new Error(`Unknown fixture repository id ${repositoryId}`);
    return repository;
  }

  requireByPath(path: string): RepositoryIdentity {
    const repository = this.byPath.get(path);
    if (repository === undefined) throw new Error(`Unknown fixture repository path ${path}`);
    return repository;
  }
}

function repository(repositoryId: string, name: string, path: string): RepositoryIdentity {
  return { repository_id: repositoryId, name, path };
}

function uniqueMap<Value>(
  values: readonly Value[],
  keyOf: (value: Value) => string,
  label: string,
): ReadonlyMap<string, Value> {
  const result = new Map<string, Value>();
  for (const value of values) {
    const key = keyOf(value);
    if (result.has(key)) throw new Error(`Duplicate fixture ${label} ${key}`);
    result.set(key, value);
  }
  return result;
}
