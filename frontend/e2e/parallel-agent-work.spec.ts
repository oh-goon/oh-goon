import { expect, test, type Page } from "@playwright/test";

import { PARALLEL_AGENT_WORK_SCENARIO, PARALLEL_BASE_REVISION, PARALLEL_TASK_ID } from "./fixtures/parallelAgentWork";
import { DEFAULT_RUN_ID, FAILED_RUN_ID } from "./fixtures/runIds";
import { gameTapeRegion } from "./helpers/locators";
import { openFixture, openRunFixture } from "./helpers/navigation";

async function expectParallelWork(
  page: Page,
  labels: { title: string; publications: string; deliveries: string; acknowledged: string },
) {
  const tape = gameTapeRegion(page);
  await tape.getByText(labels.title, { exact: true }).click();
  const panel = tape.locator("details").filter({ hasText: labels.title });
  await expect(panel.getByText(PARALLEL_TASK_ID, { exact: true })).toBeVisible();
  await expect(panel.getByText(PARALLEL_BASE_REVISION, { exact: true })).toBeVisible();
  await expect(panel.getByText(`${DEFAULT_RUN_ID} · COMPLETED`, { exact: true })).toBeVisible();
  await expect(panel.getByText(`${FAILED_RUN_ID} · FAILED`, { exact: true })).toBeVisible();
  await expect(panel.getByRole("heading", { name: labels.publications, exact: true })).toBeVisible();
  await expect(panel.getByText(`#1 · idea · ${DEFAULT_RUN_ID}`, { exact: true })).toBeVisible();
  await expect(panel.getByRole("heading", { name: labels.deliveries, exact: true })).toBeVisible();
  await expect(panel.getByText(`#1 · ${FAILED_RUN_ID} · ${labels.acknowledged}`, { exact: true })).toBeVisible();
}

test("Game Tape keeps parallel Run and note attribution when the browser language changes", async ({ page }) => {
  const scenario = PARALLEL_AGENT_WORK_SCENARIO;
  await openRunFixture(page, { scenario, view: "summary" });
  await expectParallelWork(page, {
    title: "Parallel agent work",
    publications: "Published notes (1)",
    deliveries: "Note deliveries (1)",
    acknowledged: "Acknowledged",
  });

  await openFixture(page, "settings", scenario);
  await page.getByRole("main").getByRole("combobox", { name: "Language", exact: true }).selectOption("sv");
  await openRunFixture(page, { scenario, view: "summary" });
  await expectParallelWork(page, {
    title: "Parallellt agentarbete",
    publications: "Publicerade anteckningar (1)",
    deliveries: "Leveranser av anteckningar (1)",
    acknowledged: "Bekräftad",
  });
});
