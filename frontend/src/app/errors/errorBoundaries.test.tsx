import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AppErrorBoundary } from "@/app/errors/AppErrorBoundary";
import { ComponentErrorBoundary } from "@/app/errors/ComponentErrorBoundary";
import { i18n } from "@/i18n/setup";

function Crash(): never {
  throw new Error("fixture render failure");
}

describe("error recovery boundaries", () => {
  it("keeps sibling application UI alive when a feature crashes", async () => {
    await i18n.changeLanguage("en");
    render(
      <><nav>Persistent navigation</nav><ComponentErrorBoundary area="fixture"><Crash /></ComponentErrorBoundary></>,
      { onCaughtError: () => undefined },
    );
    expect(screen.getByText("Persistent navigation")).toBeTruthy();
    expect(screen.getByText("This workspace could not be displayed")).toBeTruthy();
  });

  it("uses the fatal recovery surface for an app-level render failure", async () => {
    await i18n.changeLanguage("en");
    render(<AppErrorBoundary><Crash /></AppErrorBoundary>, { onCaughtError: () => undefined });
    expect(screen.getByText("oh-goon needs to restart")).toBeTruthy();
  });
});
