import { expect, test } from "@playwright/test";

import { openFixture } from "./helpers/navigation";
import { expectIceThemeIntegrity } from "./helpers/theme";

for (const route of ["repositories", "shared-runs", "schedules", "runs"] as const) {
  test(`${route} keeps the reviewed snow/ice browser appearance`, async ({ page }) => {
    await openFixture(page, route);
    await expectIceThemeIntegrity(page);
    await expect(page).toHaveScreenshot(`${route}.png`, { fullPage: true, animations: "disabled" });
  });
}
