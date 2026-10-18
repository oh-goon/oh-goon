import { test } from "@playwright/test";

import { openFixture } from "./helpers/navigation";
import { expectIceThemeIntegrity } from "./helpers/theme";

for (const route of ["repositories", "shared-runs", "schedules", "runs"] as const) {
  test(`${route} preserves the token-owned snow/ice design system`, async ({ page }) => {
    await openFixture(page, route);
    await expectIceThemeIntegrity(page);
  });
}
