import { expect, test, type Page } from "@playwright/test";

import { LONG_REPOSITORY_NAME } from "./fixtures/identities";
import { openFixture } from "./helpers/navigation";
import { savedPlanSelect } from "./helpers/locators";

async function openMlflow(page: Page): Promise<void> {
  await openFixture(page, "dependencies");
  await page.getByLabel("Dependency").fill("mlflow");
  await page.getByRole("button", { name: "Search" }).click();
  await page.getByRole("button", { name: /mlflow/i }).click();
  await expect(page.getByRole("heading", { level: 2, name: "mlflow" })).toBeVisible();
}


test("overview starts the observed dependency drill-down", async ({ page }: { page: Page }) => {
  await openFixture(page, "overview");
  const evidence = page.getByRole("region", { name: "Dependency evidence" });
  await expect(evidence.getByText("2", { exact: true })).toBeVisible();
  await expect(evidence).toContainText("repositories needing current dependency evidence");
  await evidence.getByRole("button", { name: "Explore dependencies" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Dependencies" })).toBeVisible();
});

test("dependency drill-down freezes an affected observed population into New run", async ({ page }: { page: Page }) => {
  await openMlflow(page);
  await expect(page.getByRole("button", { name: /Current matching evidence/ })).toContainText("4");
  await expect(page.getByRole("button", { name: /No current dependency evidence/ }).first()).toContainText("2");

  await page.getByLabel("Advisory").fill("CVE-2026-MLFLOW");
  await page.getByLabel("Affected version range").fill("<2.18.0");
  await page.getByRole("button", { name: "Apply affected range" }).click();
  await expect(page.getByText("Affected repositories").locator("..")).toContainText("2");
  await expect(page.getByText("CVE-2026-MLFLOW · <2.18.0 ×")).toBeVisible();

  await page.getByRole("button", { name: "Use all 2 in new shared run" }).click();
  await expect.poll(() => new URLSearchParams(new URL(page.url()).hash.split("?", 2)[1] ?? "").get("selection")).not.toBeNull();
  await expect(page.getByText("Observed selection · 2 repositories")).toBeVisible();
  await expect(page.getByText("CVE-2026-MLFLOW", { exact: true })).toBeVisible();
  await expect(page.getByText("<2.18.0", { exact: true })).toBeVisible();
  await savedPlanSelect(page).selectOption("python-dependency-inventory");
  await expect(page.locator(".shared-plan-selected")).toContainText("python-dependency-inventory");
  await expect(page.getByRole("checkbox", { name: new RegExp(LONG_REPOSITORY_NAME) })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: /customer-portal-web/ })).toBeChecked();
});

test("missing dependency evidence is a reviewable and selectable server population", async ({ page }: { page: Page }) => {
  await openMlflow(page);
  await page.getByRole("button", { name: /No current dependency evidence/ }).first().click();
  await expect(page.getByRole("heading", { name: "Repositories needing current dependency evidence" })).toBeVisible();
  await expect(page.getByText("catalog-service", { exact: true })).toBeVisible();
  await expect(page.getByText("reporting-api", { exact: true })).toBeVisible();
  await expect(page.getByText("No snapshot", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Use all 2 in new shared run" }).click();
  await expect(page.getByText("Observed selection · 2 repositories")).toBeVisible();
  await expect(page.getByText("No current evidence", { exact: true })).toBeVisible();
  await savedPlanSelect(page).selectOption("python-dependency-inventory");
  await expect(page.locator(".shared-plan-selected")).toContainText("python-dependency-inventory");
  await expect(page.getByRole("checkbox", { name: /catalog-service/ })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: /reporting-api/ })).toBeChecked();
});

test("dependency filters survive proof drill-down and browser history", async ({ page }: { page: Page }) => {
  await openMlflow(page);
  await page.getByRole("button", { name: /Current matching evidence/ }).click();
  await expect(page).toHaveURL(/evidence=current/);

  const firstRepository = page.locator(".dependency-repository-card").first();
  await firstRepository.getByText("Why this repository matched").click();
  await expect(firstRepository).toContainText("uv.lock");
  await firstRepository.getByRole("button", { name: "Repository dependencies" }).click();
  await expect(page).toHaveURL(/repository=/);

  await page.goBack();
  await expect(page).toHaveURL(/evidence=current/);
  await expect(page.getByRole("heading", { level: 2, name: "mlflow" })).toBeVisible();
  await expect(page.getByText("Current ×", { exact: true })).toBeVisible();

  await page.goForward();
  await expect(page).toHaveURL(/repository=/);
});
