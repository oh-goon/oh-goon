import { expect, test, type Page } from "@playwright/test";

import {
  CATALOG_FIXTURE_CANDIDATE,
  CATALOG_FIXTURE_EXECUTION_DIGEST,
  CATALOG_FIXTURE_PLAN_ID,
  CATALOG_FIXTURE_PLAN_SHA,
  CATALOG_FIXTURE_STEP_ID,
  catalogFixtureSource,
} from "./fixtures/catalog";
import { FIXTURE_REPOSITORIES, fixtureRepositoryInstanceId } from "./fixtures/identities";
import { CATALOG_EXPLORE_RELEASE_EVENT, CATALOG_REQUEST_EVENT } from "./fixtures/scenarios";
import { expectNoAutomaticAccessibilityViolations } from "./helpers/accessibility";
import { gameTapeRegion } from "./helpers/locators";
import { openFixture } from "./helpers/navigation";
import { expectIceThemeIntegrity } from "./helpers/theme";
import { expectHeadingClearOfFixedChrome, expectNoDocumentHorizontalOverflow, expectNoSilentTextClipping, expectPrimaryActionsClearOfFixedChrome, setReflowZoomEquivalent } from "./helpers/visibility";

interface CatalogRequestRecord {
  kind: "explore" | "preview" | "run" | "export";
  repositoryId: string;
  request: Record<string, unknown>;
}

declare global {
  interface Window {
    recordCatalogFixtureRequest?: (request: CatalogRequestRecord) => Promise<void>;
  }
}

const EDITED_DESCRIPTION = "Synthetic candidate review-storage sentinel.";

async function recordRequests(page: Page): Promise<CatalogRequestRecord[]> {
  const requests: CatalogRequestRecord[] = [];
  await page.exposeFunction("recordCatalogFixtureRequest", (request: CatalogRequestRecord) => { requests.push(request); });
  await page.addInitScript((eventName) => {
    window.addEventListener(eventName, (event) => {
      void window.recordCatalogFixtureRequest?.((event as CustomEvent<CatalogRequestRecord>).detail);
    });
  }, CATALOG_REQUEST_EVENT);
  return requests;
}

async function fillCandidate(page: Page): Promise<void> {
  await page.getByRole("textbox", { name: "Component name", exact: true }).fill(CATALOG_FIXTURE_CANDIDATE.name);
  await page.getByRole("textbox", { name: "Description", exact: true }).fill(CATALOG_FIXTURE_CANDIDATE.description);
  await page.getByRole("textbox", { name: "Owner reference", exact: true }).fill(CATALOG_FIXTURE_CANDIDATE.owner);
  await page.getByRole("combobox", { name: "Component type", exact: true }).selectOption(CATALOG_FIXTURE_CANDIDATE.type);
  await page.getByRole("combobox", { name: "Lifecycle", exact: true }).selectOption(CATALOG_FIXTURE_CANDIDATE.lifecycle);
  await page.getByRole("textbox", { name: /^Languages/ }).fill(CATALOG_FIXTURE_CANDIDATE.languages.join(", "));
}

async function expectNoCatalogBrowserPersistence(page: Page): Promise<void> {
  const persistence = await page.evaluate(async () => ({
    local: Object.fromEntries(Object.entries(window.localStorage)),
    session: Object.fromEntries(Object.entries(window.sessionStorage)),
    databases: (await window.indexedDB.databases()).map((database) => database.name),
    cookie: document.cookie,
    history: window.history.state,
    url: window.location.href,
  }));
  expect(Object.keys(persistence.local).every((key) => ["oh-goon.language", "oh-goon.experience"].includes(key))).toBe(true);
  expect(persistence.session).toEqual({});
  expect(persistence.databases).toEqual([]);
  expect(persistence.cookie).toBe("");
  const serialized = JSON.stringify(persistence);
  for (const value of [CATALOG_FIXTURE_CANDIDATE.description, CATALOG_FIXTURE_CANDIDATE.owner, EDITED_DESCRIPTION, "Demo Catalog Contributor", "Demo Web Contributor", "src/service.py", "/workspace/catalog-service", "/tmp/catalog-demo/catalog-info.yaml", "/tmp/catalog-demo/from-original-tape.yaml"]) {
    expect(serialized).not.toContain(value);
  }
}

test("reviewed catalog keeps its authorities, original Run and explicit retained export", async ({ page }, testInfo) => {
  const requests = await recordRequests(page);
  await openFixture(page, "catalog");
  const generate = page.getByRole("button", { name: "Generate catalog", exact: true });
  await expect(generate).not.toBeVisible();
  await page.getByRole("combobox", { name: "Repository", exact: true }).selectOption("repo-catalog");
  await page.getByRole("textbox", { name: "Search code", exact: true }).fill("submit_order");
  await page.getByRole("button", { name: "Explore repository", exact: true }).click();
  await expect(page.getByText("Select a source hit to inspect Git history.", { exact: true })).toBeVisible();
  await expect(page.getByText("Demo Catalog Contributor", { exact: true })).toHaveCount(0);
  await page.getByRole("textbox", { name: /^Git author file/ }).fill("src/service.py");
  await page.getByRole("button", { name: "Explore repository", exact: true }).click();
  await expect(page.getByText("Demo Catalog Contributor", { exact: true })).toBeVisible();
  await expect(page.getByText("Hashmarks evidence", { exact: true })).toBeVisible();
  await expect(page.getByText("Native Git author", { exact: true })).toBeVisible();
  await expect(page.getByText("Human-reviewed owner", { exact: true })).toBeVisible();
  await expect(page.getByText("Freshness", { exact: true }).locator("..")).toContainText("unknown");
  await expect(page.getByText(/call · static-name · src\/order_status.py/)).toBeVisible();
  await expect(page.getByText("Search result limits", { exact: true })).toBeVisible();
  await expect(page.getByText("complete-within-supported-scope", { exact: true })).toBeVisible();
  await fillCandidate(page);
  await page.getByRole("button", { name: "Preview catalog", exact: true }).click();
  await expect(page.getByLabel("YAML preview", { exact: true })).toContainText(CATALOG_FIXTURE_CANDIDATE.owner);
  const approval = page.getByRole("checkbox", { name: /^I approve every displayed value for this observed source/ });
  await approval.check();
  await expect(generate).toBeEnabled();

  await page.getByRole("textbox", { name: "Description", exact: true }).fill(EDITED_DESCRIPTION);
  await expect(generate).not.toBeVisible();
  await expectNoCatalogBrowserPersistence(page);
  await page.getByRole("textbox", { name: "Description", exact: true }).fill(CATALOG_FIXTURE_CANDIDATE.description);
  await page.getByRole("button", { name: "Preview catalog", exact: true }).click();
  await expect(approval).not.toBeChecked();
  await approval.focus();
  await page.keyboard.press("Space");
  await expect(approval).toBeChecked();
  await expectNoAutomaticAccessibilityViolations(page);
  await expectIceThemeIntegrity(page);
  await expectNoDocumentHorizontalOverflow(page);
  await expectNoSilentTextClipping(page);
  await setReflowZoomEquivalent(page, 2);
  await expectNoDocumentHorizontalOverflow(page);
  await expectNoSilentTextClipping(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await expectNoDocumentHorizontalOverflow(page);
  await expectNoSilentTextClipping(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); window.scrollTo(0, 0); });
  await expectHeadingClearOfFixedChrome(page);
  await expectPrimaryActionsClearOfFixedChrome(page);
  await page.screenshot({ path: testInfo.outputPath("catalog-reviewed-preview.png"), fullPage: true, animations: "disabled" });
  await expectNoCatalogBrowserPersistence(page);

  await generate.click();
  await expect(page.getByRole("button", { name: "Export retained artifact", exact: true })).toBeVisible();
  const runRequest = requests.find((request) => request.kind === "run");
  expect(runRequest?.request).toMatchObject({
    source_authority: catalogFixtureSource(FIXTURE_REPOSITORIES.catalog), candidate: CATALOG_FIXTURE_CANDIDATE,
    expected_plan_id: CATALOG_FIXTURE_PLAN_ID, expected_plan_sha256: CATALOG_FIXTURE_PLAN_SHA,
    expected_execution_digest: CATALOG_FIXTURE_EXECUTION_DIGEST,
  });
  const runId = runRequest?.request.fleet_run_id;
  expect(typeof runId).toBe("string");
  await page.getByRole("textbox", { name: "Export destination", exact: true }).fill("/tmp/catalog-demo/catalog-info.yaml");
  await page.getByRole("button", { name: "Export retained artifact", exact: true }).click();
  await expect(page.getByText("Retained artifact exported", { exact: true })).toBeVisible();
  await expect.poll(() => requests.filter((request) => request.kind === "export").length).toBe(1);
  expect(requests.find((request) => request.kind === "export")?.request).toMatchObject({
    runId, repository_id: "repo-catalog", step_id: CATALOG_FIXTURE_STEP_ID,
    path: "catalog-info.yaml", destination: "/tmp/catalog-demo/catalog-info.yaml",
  });
  expect(requests.find((request) => request.kind === "explore")?.request).toMatchObject({
    expected_repository_instance_id: fixtureRepositoryInstanceId("repo-catalog"), query: "submit_order",
  });
  expect(requests.filter((request) => request.kind === "explore")[1]?.request).toMatchObject({ path: "src/service.py" });
  await expectNoCatalogBrowserPersistence(page);

  await page.getByRole("button", { name: "Open original Game Tape", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`run=${runId}`));
  await expect(gameTapeRegion(page)).toBeVisible();
  await gameTapeRegion(page).getByRole("button", { name: "Outputs", exact: true }).click();
  await expect(gameTapeRegion(page).getByText("catalog-info.yaml", { exact: true }).first()).toBeVisible();
  await gameTapeRegion(page).getByRole("textbox", { name: "Export destination", exact: true }).fill("/tmp/catalog-demo/from-original-tape.yaml");
  await gameTapeRegion(page).getByRole("button", { name: "Export retained artifact", exact: true }).click();
  await expect(gameTapeRegion(page).getByText("Retained artifact exported", { exact: true })).toBeVisible();
  await expect.poll(() => requests.filter((request) => request.kind === "export").length).toBe(2);
  expect(requests.filter((request) => request.kind === "export").map((request) => request.request)).toEqual([
    { runId, repository_id: "repo-catalog", step_id: CATALOG_FIXTURE_STEP_ID, path: "catalog-info.yaml", destination: "/tmp/catalog-demo/catalog-info.yaml" },
    { runId, repository_id: "repo-catalog", step_id: CATALOG_FIXTURE_STEP_ID, path: "catalog-info.yaml", destination: "/tmp/catalog-demo/from-original-tape.yaml" },
  ]);
  await expectNoCatalogBrowserPersistence(page);
});

test("late discovery cannot attach another repository's source or author", async ({ page }) => {
  const requests = await recordRequests(page);
  await openFixture(page, "catalog", "catalog-held");
  const repository = page.getByRole("combobox", { name: "Repository", exact: true });
  await repository.selectOption("repo-catalog");
  await page.getByRole("textbox", { name: "Search code", exact: true }).fill("submit_order");
  await page.getByRole("textbox", { name: /^Git author file/ }).fill("src/service.py");
  await page.getByRole("button", { name: "Explore repository", exact: true }).click();
  await expect.poll(() => requests.filter((request) => request.kind === "explore").length).toBe(1);
  await repository.selectOption("repo-web");
  await page.getByRole("textbox", { name: "Search code", exact: true }).fill("submit_order");
  await page.getByRole("textbox", { name: /^Git author file/ }).fill("src/service.py");
  await page.getByRole("button", { name: "Explore repository", exact: true }).click();
  await expect(page.getByText("Demo Web Contributor", { exact: true })).toBeVisible();
  await page.evaluate((eventName) => { window.dispatchEvent(new Event(eventName)); }, CATALOG_EXPLORE_RELEASE_EVENT);
  await expect(page.getByText("Demo Catalog Contributor", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Demo Web Contributor", { exact: true })).toBeVisible();
  await expect(repository).toHaveValue("repo-web");
  await expect(page.getByRole("button", { name: "Generate catalog", exact: true })).not.toBeVisible();
  await expectNoCatalogBrowserPersistence(page);
});
