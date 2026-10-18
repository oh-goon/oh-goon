import { expect, test } from "@playwright/test";

import { browserCapabilities } from "./capabilityMatrix";
import { enableAdvancedExperience, openFixture } from "./helpers/navigation";

for (const capability of browserCapabilities.filter((item) => item.standard || item.page === "settings" || item.page === "help")) {
  test(`${capability.page} opens without browser errors`, async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
    page.on("pageerror", (error) => errors.push(error.message));
    await openFixture(page, capability.page);
    await expect(page.getByRole("heading", { level: 1, name: capability.heading })).toBeVisible();
    expect(errors).toEqual([]);
  });
}

test("Advanced experience exposes Recovery and Tools without changing standard page identities", async ({ page }) => {
  await enableAdvancedExperience(page);
  await openFixture(page, "repositories");
  await expect(page.getByRole("button", { name: "Recovery" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Tools" })).toBeVisible();
});
