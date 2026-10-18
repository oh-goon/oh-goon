import { expect, test } from "@playwright/test";

import { openFixture } from "./helpers/navigation";

const emptyCases = [
  { route: "repositories", text: "Add a repository location" },
  { route: "shared-runs", text: "No repositories available" },
  { route: "schedules", text: "No runnable repositories yet" },
  { route: "runs", text: "No runs yet" },
] as const;

for (const item of emptyCases) {
  test(`${item.route} has an actionable resolved-empty state`, async ({ page }) => {
    await openFixture(page, item.route, "empty");
    await expect(page.getByText(item.text, { exact: false }).first()).toBeVisible();
  });
}

test("empty Runs points to the two normal ways to start work", async ({ page }) => {
  await openFixture(page, "runs", "empty");
  await expect(page.getByRole("button", { name: "Open Repositories" })).toBeVisible();
  await expect(page.getByRole("button", { name: "New shared run" })).toHaveCount(1);
});
