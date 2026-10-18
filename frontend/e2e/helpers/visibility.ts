import { expect, type Page } from "@playwright/test";

interface ClipViolation {
  selector: string;
  text: string;
  overflowX: string;
  overflowY: string;
  clientWidth: number;
  scrollWidth: number;
  clientHeight: number;
  scrollHeight: number;
}

export async function expectNoSilentTextClipping(page: Page): Promise<void> {
  const violations = await page.evaluate(() => {
    const selectorFor = (element: Element): string => {
      const node = element as HTMLElement;
      if (node.id) return `#${node.id}`;
      const className = typeof node.className === "string" ? node.className.trim().split(/\s+/).slice(0, 3).join(".") : "";
      return `${node.tagName.toLowerCase()}${className ? `.${className}` : ""}`;
    };
    const clipped: ClipViolation[] = [];
    for (const candidate of Array.from(document.body.querySelectorAll("*"))) {
      if (!(candidate instanceof HTMLElement)) continue;
      const element = candidate;
      if (element.closest(".sr-only") || element.closest("details:not([open])") || element.getClientRects().length === 0) continue;
      const text = element.innerText.trim();
      if (!text || element.children.length > 0) continue;
      const style = getComputedStyle(element);
      const hiddenX = style.overflowX === "hidden" || style.overflowX === "clip";
      const hiddenY = style.overflowY === "hidden" || style.overflowY === "clip";
      if ((hiddenX && element.scrollWidth > element.clientWidth + 1) || (hiddenY && element.scrollHeight > element.clientHeight + 1)) {
        clipped.push({
          selector: selectorFor(element), text: text.slice(0, 160), overflowX: style.overflowX, overflowY: style.overflowY,
          clientWidth: element.clientWidth, scrollWidth: element.scrollWidth, clientHeight: element.clientHeight, scrollHeight: element.scrollHeight,
        });
      }
    }
    return clipped;
  });
  expect(violations, `Visible text must not be silently clipped: ${JSON.stringify(violations, null, 2)}`).toEqual([]);
}

export async function expectNoDocumentHorizontalOverflow(page: Page): Promise<void> {
  const report = await page.evaluate(() => {
    const root = document.documentElement;
    const client = root.clientWidth;
    const scroll = root.scrollWidth;
    if (scroll <= client + 1) return { client, scroll, offenders: [] as Array<Record<string, unknown>> };
    const selectorFor = (element: Element): string => {
      const node = element as HTMLElement;
      if (node.id) return `#${node.id}`;
      const className = typeof node.className === "string" ? node.className.trim().split(/\s+/).slice(0, 4).join(".") : "";
      return `${node.tagName.toLowerCase()}${className ? `.${className}` : ""}`;
    };
    const offenders = Array.from(document.body.querySelectorAll<HTMLElement>("*"))
      .filter((element) => element.getClientRects().length > 0)
      .map((element) => {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        const ownerChain: string[] = [];
        let ancestor = element.parentElement;
        while (ancestor && ownerChain.length < 6) {
          const ancestorStyle = getComputedStyle(ancestor);
          if (["auto", "scroll", "clip", "hidden"].includes(ancestorStyle.overflowX)) {
            ownerChain.push(`${selectorFor(ancestor)}[overflow-x=${ancestorStyle.overflowX}]`);
          }
          ancestor = ancestor.parentElement;
        }
        return {
          selector: selectorFor(element),
          left: Math.round(rect.left),
          right: Math.round(rect.right),
          width: Math.round(rect.width),
          clientWidth: element.clientWidth,
          scrollWidth: element.scrollWidth,
          overflowX: style.overflowX,
          ownerChain,
        };
      })
      .filter((item) => item.right > client + 1 || item.left < -1 || item.scrollWidth > item.clientWidth + 1 && item.overflowX === "visible")
      .sort((a, b) => Math.max(b.right - client, b.scrollWidth - b.clientWidth) - Math.max(a.right - client, a.scrollWidth - a.clientWidth))
      .slice(0, 16);
    return { client, scroll, offenders };
  });
  expect(report.scroll, `Document overflowed horizontally: ${JSON.stringify(report, null, 2)}`).toBeLessThanOrEqual(report.client + 1);
}

export async function expectHeadingClearOfFixedChrome(page: Page): Promise<void> {
  const overlap = await page.evaluate(() => {
    const heading = document.querySelector<HTMLElement>("main h1");
    const bar = document.querySelector<HTMLElement>(".app-bar");
    if (!heading || !bar) return null;
    const headingRect = heading.getBoundingClientRect();
    const barRect = bar.getBoundingClientRect();
    return { headingTop: headingRect.top, barBottom: barRect.bottom };
  });
  expect(overlap).not.toBeNull();
  if (overlap) expect(overlap.headingTop).toBeGreaterThanOrEqual(overlap.barBottom - 1);
}

/** Browser zoom reduces the CSS viewport. Halving the viewport is a deterministic 200% reflow equivalent. */
export async function setReflowZoomEquivalent(page: Page, factor: number): Promise<void> {
  const viewport = page.viewportSize();
  if (!viewport) throw new Error("Playwright viewport is required for the reflow gate");
  await page.setViewportSize({ width: Math.round(viewport.width / factor), height: viewport.height });
}
export async function expectPrimaryActionsClearOfFixedChrome(page: Page): Promise<void> {
  const overlaps = await page.evaluate(() => {
    const visible = (element: HTMLElement): boolean => element.getClientRects().length > 0 && getComputedStyle(element).visibility !== "hidden";
    const intersects = (first: DOMRect, second: DOMRect): boolean => first.left < second.right - 1 && first.right > second.left + 1 && first.top < second.bottom - 1 && first.bottom > second.top + 1;
    const chrome = Array.from(document.body.querySelectorAll<HTMLElement>("*"))
      .filter((element) => visible(element) && ["fixed", "sticky"].includes(getComputedStyle(element).position));
    const problems: Array<{ action: string; chrome: string }> = [];
    for (const action of Array.from(document.querySelectorAll<HTMLElement>(".button--primary"))) {
      if (!visible(action)) continue;
      const actionRect = action.getBoundingClientRect();
      for (const layer of chrome) {
        if (layer === action || layer.contains(action) || action.contains(layer)) continue;
        if (intersects(actionRect, layer.getBoundingClientRect())) {
          problems.push({ action: action.innerText.trim().slice(0, 100), chrome: layer.className || layer.tagName.toLowerCase() });
        }
      }
    }
    return problems;
  });
  expect(overlaps, `Primary actions must not be covered by fixed/sticky chrome: ${JSON.stringify(overlaps, null, 2)}`).toEqual([]);
}

