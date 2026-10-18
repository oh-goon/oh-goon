
test("5,000-repository execution grid stays read-only, bounded, filterable, and drills into canonical Runs", async ({ page }) => {
  await openFixture(page, "overview", "scale");
  const grid = page.getByRole("region", { name: "Execution grid", exact: true });
  await expect(grid.locator("tbody tr")).toHaveCount(100);
  await expect(grid).toContainText("1–100 of 5000 repositories");

  await grid.getByLabel("Repository").fill("service-04999");
  await expect(grid.locator("tbody tr")).toHaveCount(1);
  await expect(grid.getByText("service-04999", { exact: true })).toBeVisible();

  await grid.getByLabel("Repository").fill("");
  await grid.getByLabel("Plan").fill(LONG_PLAN_NAME);
  await expect(grid).toContainText("1–100 of 5000 repositories");
  await grid.getByLabel("Plan").fill("plan-that-does-not-exist");
  await expect(grid.getByText("No recorded executions matched this period and filter.")).toBeVisible();

  await grid.getByLabel("Plan").fill("");
  await grid.getByRole("button", { name: "Week" }).click();
  await grid.getByLabel("Plan").fill(LONG_PLAN_NAME);
  await grid.getByLabel("Repository").fill("service-04999");
  await expect(grid.locator("tbody tr")).toHaveCount(1);
  const filteredRow = grid.locator("tbody tr").first();
  await filteredRow.locator("button.execution-timeline__cell").first().click();
  await expect(page).toHaveURL(/#runs\?.*repository=repo-scale-04999.*history_from=.*history_to=.*history_plan=/);
  const history = runHistoryRegion(page);
  await expect(history.locator(".history-row")).toHaveCount(1);
  await expect(gameTapeRegion(page)).toHaveCount(0);
  await history.locator(".history-row").first().click();
  await expectExplicitRunRoute(page, { repository: true });

  await page.goBack();
  await expect(page).toHaveURL(/#runs\?.*repository=repo-scale-04999.*history_from=.*history_to=.*history_plan=/);
  await expect(gameTapeRegion(page)).toHaveCount(0);
  await page.goBack();
  await expect(page).toHaveURL(/#overview\?.*period=week.*plan=.*repository=service-04999/);
  await expect(grid.getByRole("button", { name: "Week" })).toHaveAttribute("aria-pressed", "true");
  await expect(grid.getByLabel("Plan")).toHaveValue(LONG_PLAN_NAME);
  await expect(grid.getByLabel("Repository")).toHaveValue("service-04999");
  await expect(grid.locator("tbody tr")).toHaveCount(1);
});

import { expect, test } from "@playwright/test";

import { enableAdvancedExperience, expectExplicitRunRoute, openFixture, openRunFixture } from "./helpers/navigation";
import { currentRunList, currentRunSummary, gameTapeFlowSearch, gameTapeInspector, gameTapeRegion, runHistoryRegion, savedPlanSelect } from "./helpers/locators";
import { LONG_PLAN_NAME, LONG_STEP_NAME } from "./fixtures/identities";

test("5,000-repository fleet keeps primary repository surfaces DOM-bounded and searchable", async ({ page }) => {
  await enableAdvancedExperience(page);
  await openFixture(page, "repositories", "scale");

  const league = page.locator(".league-map-panel");
  const inventory = page.locator(".repository-panel");
  await expect(league.locator("tbody tr")).toHaveCount(60);
  await expect(inventory.locator("tbody tr")).toHaveCount(50);
  await expect(inventory.getByText("1–50 of 5000 repositories")).toBeVisible();

  await league.getByLabel("Find repository").fill("service-04999");
  await expect(league.getByText("service-04999", { exact: true })).toBeVisible();
  await expect(league.locator("tbody tr")).toHaveCount(1);

  await inventory.getByLabel("Search repositories…").fill("service-04999");
  await expect(inventory.getByText("service-04999", { exact: true })).toBeVisible();
  await expect(inventory.locator("tbody tr")).toHaveCount(1);
});


test("5,000-repository Shared Run stays Plan-first, truthful, and DOM-bounded through results", async ({ page }) => {
  await openFixture(page, "shared-runs", "scale");
  await savedPlanSelect(page).selectOption("python-quality");
  const selector = page.locator(".repository-selector");
  await expect(selector.locator(".repository-selector__row")).toHaveCount(100);
  await expect(selector.getByText("1–100 of 5000 matching repositories")).toBeVisible();

  await selector.getByLabel("Find repositories").fill("service-04999");
  await expect(selector.getByText("service-04999", { exact: true })).toBeVisible();
  await expect(selector.locator(".repository-selector__row")).toHaveCount(1);

  await selector.getByLabel("Find repositories").fill("");
  await selector.getByRole("button", { name: "Select all 5000" }).click();
  await expect(selector).toContainText("5000 selected · 5000 total");
  const verification = page.locator(".backend-verification");
  await expect(page.locator(".backend-verification--ready")).toBeVisible();
  await expect(page.getByRole("button", { name: /Run on 5000/ })).toBeEnabled();
  await verification.getByText("Technical verification details").click();
  await expect(verification.locator("details.advanced-disclosure > ul.compact-list > li")).toHaveCount(100);
  await expect(verification.getByText("1–100 of 5000 matching repositories")).toBeVisible();

  await page.getByRole("button", { name: /Run on 5000/ }).click();
  await expect(page.getByRole("heading", { name: "Run result" })).toBeVisible();
  const result = currentRunSummary(page);
  const resultList = currentRunList(page);
  await expect(result.getByRole("button", { name: /5000 Passed/ })).toBeVisible();
  await expect(resultList.getByRole("button", { name: /5000 Passed/ })).toHaveCount(0);
  await result.getByRole("button", { name: /5000 Passed/ }).click();
  await expect(resultList.getByRole("listitem")).toHaveCount(100);
  await expect(page.getByText("1–100 of 5000 repositories")).toBeVisible();
});

test("fleet-scale dependency search and repository evidence pages reach results beyond the first bound", async ({ page }) => {
  await openFixture(page, "dependencies", "scale");
  const dependencySearch = page.locator(".dependency-search-panel").getByRole("search");
  await dependencySearch.getByLabel("Dependency", { exact: true }).fill("package");
  await dependencySearch.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByText("1–30 of 65")).toBeVisible();
  await page.getByRole("navigation", { name: "Dependency search result pages", exact: true }).getByRole("button", { name: "Next", exact: true }).click();
  await expect(page).toHaveURL(/offset=30/);
  await expect(page.getByText("31–60 of 65")).toBeVisible();
  await expect(page.getByText("package-031", { exact: true })).toBeVisible();

  await dependencySearch.getByLabel("Dependency", { exact: true }).fill("mlflow");
  await dependencySearch.getByRole("button", { name: "Search", exact: true }).click();
  await page.locator(".dependency-search-results").getByRole("button", { name: /^mlflow/i }).click();
  await page.locator(".dependency-repository-card").first().getByRole("button", { name: "Repository dependencies" }).click();
  await expect(page.getByText("1–100 of 240")).toBeVisible();
  await expect(page.getByText("scale-package-001", { exact: true })).toBeVisible();
  await page.getByRole("navigation", { name: "Repository dependency pages", exact: true }).getByRole("button", { name: "Next", exact: true }).click();
  await expect.poll(() => {
    const params = new URLSearchParams(new URL(page.url()).hash.split("?", 2)[1] ?? "");
    return { repository: params.get("repository"), offset: params.get("offset") };
  }).toEqual({ repository: "repo-scale-00000", offset: "100" });
  await expect(page.getByText("101–200 of 240")).toBeVisible();
  await expect(page.getByText("scale-package-101", { exact: true })).toBeVisible();
});

test("5,000-repository dependency incident preserves fleet truth and materializes the exact affected population", async ({ page }) => {
  await openFixture(page, "dependencies", "scale");
  const dependencySearch = page.locator(".dependency-search-panel").getByRole("search");
  await dependencySearch.getByLabel("Dependency", { exact: true }).fill("mlflow");
  await dependencySearch.getByRole("button", { name: "Search", exact: true }).click();
  const result = page.locator(".dependency-search-results").getByRole("button", { name: /^mlflow/i });
  await expect(result).toContainText("137 repositories");
  await result.click();

  const coverage = page.locator(".dependency-package-panel > .dependency-coverage-grid").first();
  await expect(coverage.getByRole("button", { name: /Current matching evidence/ })).toContainText("137");
  await expect(coverage.getByRole("button", { name: /No current dependency evidence/ })).toContainText("152");
  await page.getByLabel("Advisory").fill("CVE-2026-MLFLOW");
  await page.getByLabel("Affected version range").fill("<2.18.0");
  await page.getByRole("button", { name: "Apply affected range" }).click();
  const advisorySummary = page.locator(".dependency-advisory-summary");
  await expect(advisorySummary.getByRole("button", { name: /Affected repositories/ })).toContainText("92");
  await expect(advisorySummary.getByRole("button", { name: /No current dependency evidence/ })).toContainText("152");

  await page.getByRole("button", { name: "Use all 92 in new shared run" }).click();
  await expect.poll(() => new URLSearchParams(new URL(page.url()).hash.split("?", 2)[1] ?? "").get("selection")).not.toBeNull();
  await expect(page.getByText("Observed selection · 92 repositories")).toBeVisible();
});

test("5,000-target Run review keeps target details and Game Tape lanes DOM-bounded", async ({ page }) => {
  await openRunFixture(page, { scenario: "scale" });

  const detail = page.locator(".run-detail");
  await expect(detail.locator(".operation-result-card")).toHaveCount(50);
  await expect(detail.getByText("1–50 of 5000 repositories")).toBeVisible();

  const gameTape = gameTapeRegion(page);
  await expect(gameTape.locator(".game-tape-flowchart__lane")).toHaveCount(40);
  await expect(gameTape.getByText("1–40 of 5000 repositories")).toBeVisible();

  const flowSearch = gameTapeFlowSearch(page);
  await flowSearch.getByRole("searchbox", { name: "Find in CI/CD Flow", exact: true }).fill("service-04999");
  const nextMatch = flowSearch.getByRole("button", { name: "Next", exact: true });
  await expect(nextMatch).toBeEnabled();
  await nextMatch.click();
  await expect(gameTape.getByText("4961–5000 of 5000 repositories")).toBeVisible();
  await expect(gameTape.locator(".game-tape-flowchart__selected-dock")).toContainText("service-04999");

  await gameTape.getByRole("button", { name: "Timeline" }).click();
  await expect(gameTape.locator(".game-tape-timeline__lane-heading")).toHaveCount(40);
  await expect(gameTape.getByText("4961–5000 of 5000 repositories")).toBeVisible();

  await gameTape.getByRole("button", { name: "Overview" }).click();
  await expect(gameTape.locator(".game-tape-plan-actual > article")).toHaveCount(50);
});

test("fleet-scale failure investigation preserves the Overview filter and lands on exact failed Step evidence", async ({ page }) => {
  await openFixture(page, "overview", "scale");
  await page.getByRole("button", { name: /Failed\s+188/ }).click();
  await expect(page).toHaveURL(/history_outcome=FAILED/);
  const history = runHistoryRegion(page);
  await expect(gameTapeRegion(page)).toHaveCount(0);
  const failedRun = history.locator(".history-row").filter({ hasText: "service-00092" }).first();
  await expect(failedRun).toBeVisible();
  await failedRun.click();
  await expectExplicitRunRoute(page);
  const gameTape = gameTapeRegion(page);
  await gameTape.getByRole("button", { name: "Steps", exact: true }).click();
  await gameTape.getByRole("button", { name: "Jump to Failed Step", exact: true }).click();
  const inspector = gameTapeInspector(page);
  await expect(inspector).toBeVisible();
  await expect(inspector).toContainText("validate-generated-contract-and-production-security-evidence-with-realistic-long-step-name");
  await expect(inspector).toContainText("The exact Step failure text is intentionally long so browser visibility tests can verify wrapping, zoom and evidence readability.");
});

test("5,000-repository failure inventory stays bounded, searchable, and drills into exact Game Tape evidence", async ({ page }) => {
  await openFixture(page, "overview", "scale");
  const failureEntry = page.getByRole("region", { name: "Failing repositories", exact: true });
  await expect(failureEntry).toContainText("5,000");
  await failureEntry.getByRole("button", { name: "Investigate failures" }).click();
  await expect(page).toHaveURL(/#failures/);

  const groups = page.getByRole("region", { name: "Common failure locations", exact: true });
  await expect(groups.locator("tbody tr")).toHaveCount(50);
  await expect(groups).toContainText("1–50 of 121");

  await page.getByLabel("Search failure groups").fill("Python production quality");
  await expect(groups.locator("tbody tr")).toHaveCount(1);
  await groups.getByRole("button", { name: LONG_PLAN_NAME }).click();

  const repositories = page.getByRole("region", { name: "Affected repositories", exact: true });
  await expect(repositories.locator("tbody tr")).toHaveCount(50);
  await expect(repositories).toContainText("1–50 of 5000");
  await page.getByLabel("Search repositories").fill("service-04999");
  await expect(repositories.getByText("service-04999", { exact: true })).toBeVisible();
  await expect(repositories.locator("tbody tr")).toHaveCount(1);
  await repositories.getByRole("button", { name: "Open Game Tape" }).click();

  await expectExplicitRunRoute(page);
  await expect(page).toHaveURL(/focus=failures/);
  const inspector = gameTapeInspector(page);
  await expect(inspector).toBeVisible();
  await expect(inspector).toContainText(LONG_STEP_NAME);
});
