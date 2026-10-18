import { expect, type Page } from "@playwright/test";

export async function expectIceThemeIntegrity(page: Page): Promise<void> {
  const theme = await page.evaluate(() => {
    const root = getComputedStyle(document.documentElement);
    const body = getComputedStyle(document.body);
    const panel = document.querySelector<HTMLElement>(".panel");
    const panelStyle = panel ? getComputedStyle(panel) : null;
    const textOwner = panel?.querySelector<HTMLElement>("h1,h2,h3,p,strong,span,label,button") ?? null;
    const textStyle = textOwner ? getComputedStyle(textOwner) : null;
    const panelRect = panel?.getBoundingClientRect();
    return {
      tokens: {
        surfacePanel: root.getPropertyValue("--surface-panel").trim(),
        surfaceCanvas: root.getPropertyValue("--surface-canvas").trim(),
        textPrimary: root.getPropertyValue("--text-primary").trim(),
        textMuted: root.getPropertyValue("--text-muted").trim(),
        actionPrimary: root.getPropertyValue("--action-primary").trim(),
        actionPrimarySoft: root.getPropertyValue("--action-primary-soft").trim(),
        focus: root.getPropertyValue("--border-focus").trim(),
        iceLine: root.getPropertyValue("--ice-rink-line").trim(),
        frost: root.getPropertyValue("--ice-frost-line").trim(),
        canvasTreatment: root.getPropertyValue("--ice-canvas-background").trim(),
        panelTreatment: root.getPropertyValue("--ice-panel-background").trim(),
      },
      body: {
        color: body.color,
        visibility: body.visibility,
      },
      panel: panelStyle && panelRect ? {
        visibility: panelStyle.visibility,
        opacity: panelStyle.opacity,
        width: panelRect.width,
        height: panelRect.height,
      } : null,
      text: textStyle ? {
        color: textStyle.color,
        visibility: textStyle.visibility,
        opacity: textStyle.opacity,
      } : null,
    };
  });

  for (const value of Object.values(theme.tokens)) expect(value).not.toBe("");
  expect(theme.body.visibility).toBe("visible");
  expect(theme.body.color).not.toBe("rgba(0, 0, 0, 0)");
  expect(theme.panel).not.toBeNull();
  expect(theme.panel!.visibility).toBe("visible");
  expect(Number(theme.panel!.opacity)).toBeGreaterThan(0);
  expect(theme.panel!.width).toBeGreaterThan(0);
  expect(theme.panel!.height).toBeGreaterThan(0);
  expect(theme.text).not.toBeNull();
  expect(theme.text!.visibility).toBe("visible");
  expect(Number(theme.text!.opacity)).toBeGreaterThan(0);
  expect(theme.text!.color).not.toBe("rgba(0, 0, 0, 0)");
}
