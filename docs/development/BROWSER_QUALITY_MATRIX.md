# Browser quality matrix

> **Browser capability reference — not an APP or release orchestration path.** APP browser gates are invoked by `oh-goon/app-certification`. Junior agents should follow `APP_TEST_RUN.md` rather than constructing a certification sequence from the commands in this document.


909 makes browser quality an executable product contract rather than a subjective polish pass.

## Hard quality hierarchy

1. **Information is present.** The browser must expose every normal user capability and its important operational facts.
2. **Information is readable.** Critical text may wrap, expand, scroll locally, or live behind an explicit disclosure; it must not be silently clipped or truncated.
3. **Interaction is obvious.** Disabled/destructive/next-step states explain themselves and primary actions remain reachable.
4. **Accessibility works.** Keyboard, focus, semantic labels, WCAG A/AA automation and 200% reflow/zoom are exercised in a real browser.
5. **Theme stays distinctive.** The established snow/ice/hockey visual identity remains token-owned and cannot compete with items 1–4.

> Never sacrifice information for layout. Wrap, expand, scroll, or provide details—but never silently truncate or hide operational information.

## Capability coverage

| Surface | Browser journey | Critical visible information | Quality coverage |
| --- | --- | --- | --- |
| Repositories | find repository → Run CI/CD → current Run → Runs | repository name, backend status/blocker, action | Playwright journey, long repository fixture, mobile/reflow, axe |
| New run | Plan → repositories → backend verification → Run | selected Plan, exact repository scope, backend verification/blockers | Playwright journey, long Plan/repository fixtures, fail-closed action visibility, axe |
| Schedules | create → edit → pause/enable → Run now → Game Tape | repository, Plan, friendly frequency, execution state | Playwright journey, long names, form semantics, axe |
| Runs/Game Tape | latest/deep link → Steps (CI/CD Flow) → Overview → Timeline → Logs → technical evidence | Plan/Goon/Step sequence, lane navigation/status scoreboard, non-destructive Flow finder, repository outcomes, long Step/error/evidence text, recovery guidance | Playwright multi-lane journey, clipping/zoom gate, axe, reduced-motion navigation, visual regression |
| Settings/Help | utility navigation | preference/folder/help text | route smoke + accessibility |
| Recovery/Tools | Advanced experience | operator/recovery facts | Advanced route smoke; deeper operator tests remain explicit |


## Backend truth boundary

Browser quality includes architectural fidelity, not only visual polish. Backend/generated contracts own execution identity, verification/admission decisions, Run lifecycle, and Game Tape evidence. The browser may search/filter repositories, preserve investigation context, request read-only verification, summarize returned facts, and progressively disclose details. It must not introduce a second readiness/admission model, silently convert a blocked whole selection into partial execution, or add wizard state that becomes execution authority. Shared Plan is the reference flow: **Plan → repositories → backend verification → Run**.

The reference flow is also a performance boundary. Opening New run must not preload Plan history, Game Tape, saved selections, Plan templates, or editable Plan documents. Those facts either belong to Runs/Game Tape or are fetched only after the user opens the relevant Advanced disclosure. A successful Run write navigates to the exact canonical Run; a failed write leaves the current selection intact.

At fleet scale, browser selection state is never authority over a newer backend catalog. Repository and Plan IDs that disappear from the latest catalog are removed from the form before an action can be presented as valid. Blocked repository removal is an explicit user edit to the target set, followed by a new backend verification; it is never automatic partial execution.

## Browser matrix

`frontend/playwright.config.ts` defines Chromium, Firefox and WebKit desktop projects plus explicit Chromium 390px mobile and 768px tablet projects. Release acceptance runs the complete product journey suite once in Chromium. Firefox and WebKit rerun the accessibility, content-visibility and route compatibility smoke; mobile and tablet rerun content visibility, fleet scale and route smoke. The installed production bundle still runs its focused runtime journey in all three browser engines.

## Content visibility gate

`content-visibility.spec.ts` fails when:

- visible leaf text is actually clipped by `overflow: hidden/clip`,
- the document develops unintended horizontal overflow,
- fixed/sticky app chrome covers the active page heading or a visible primary action,
- or the same problems appear on any of the four everyday surfaces under the deterministic 200% reflow-equivalent gate (desktop CSS viewport halved, matching browser-zoom reflow pressure).

The fixture deliberately uses long repository, Plan and Step identities plus long failure text. Authored CSS is also statically forbidden from using `text-overflow: ellipsis` or line-clamp truncation for product information.


### Source-level contrast floor

`make theme-contrast` (uv-backed) is dependency-independent and validates the canonical `themeTokens.ts` palette before a browser is available. Primary/secondary/muted/link text across supported dark surfaces, primary-button text and every semantic status foreground/surface pair must remain at least **4.5:1**. Axe still validates the rendered browser combinations; this source gate prevents an agent from making the ice palette unreadable before Playwright even runs.

## Accessibility gate

`@axe-core/playwright` runs WCAG A/AA automation on the four standard product surfaces. Keyboard coverage includes Skip to content and route focus. Axe is not treated as a replacement for content-visibility or interaction tests.

## Theme integrity gate

`theme-integrity.spec.ts` is the dependency-backed hard theme gate and checks semantic ice/rink variables on each standard surface. Focused Chromium screenshots live separately in `visual-regression.visual.ts` and run through `playwright.visual.config.ts`, so the normal browser journey suite never requires an unreviewed baseline update. Visual changes should normally be made through `themeTokens.ts`; snapshot baseline updates must be deliberate and reviewed. Accessibility fixes must not introduce a generic replacement theme.

## Release command

Fresh and existing checkouts use `make init`, which delegates locked backend/frontend materialization directly to native uv/npm. Browser engines are intentionally separate; `make browser-setup` installs only the pinned browser binaries and does not invoke the OS package manager. `make browser-system-deps` is the explicit privileged operation for Playwright host libraries. `make browser-preflight` launches all three engines before any matrix work so missing host dependencies produce one actionable failure. `make theme-contrast` is the dependency-independent palette gate.

The lower-level dependency-backed browser release diagnostic is: the right-sized multi-engine/responsive matrix followed by reviewed Chromium visual regression. `make acceptance-browser-container` derives a pinned official Playwright image from `frontend/package-lock.json` and runs the complete build, fixture browser, visual, clean-wheel, production-runtime browser and restart sequence inside that same environment. Ordinary `npm run check` remains browser-independent.
## Folded-lane visibility rule (922)

Explicitly folded repository lanes must still state their recorded Step count, recorded source-segment count, canonical Step-status breakdown and that details were hidden by the user. The complete lane remains one labelled disclosure away, and any canonical Step navigation reopens it before focus. Folding is never an information-deletion or auto-hiding mechanism.
## Status and Timeline visual-comprehension rule (937)

Flow and Timeline must make canonical Step outcome immediately understandable through **symbol + literal status text + semantic color**. Color is reinforcement, never the sole carrier. Timeline bars share one recorded wall-clock X axis with explicit Start/End and elapsed ticks. The browser may use recorded `duration_ms` for an open Step but may not invent a current-time endpoint. Long Step labels and status text must remain accessible at supported viewport/reflow sizes.
## Selected-Step Timeline focus rule (938)

Selecting a Step may add a visible timing card and X-axis cursor, but both must reuse the canonical recorded interval model. Finished Steps use recorded Start/End; open Steps may show a recorded-through endpoint derived only from recorded duration. Cursor/bar/row color reinforces the literal symbol + status text and never becomes the sole meaning.
## Timeline scale truth

Timeline is a bounded visualization: recorded duration changes the labelled elapsed-time domain, never the canvas width. The rightmost ruler tick is a rounded **scale end**, not the recorded Run end. Very short Steps retain their exact proportional width and remain visible through the endpoint status puck.

Run comparisons must never auto-fit independently. The Run Diff duration graphic uses one explicit shared `0 → max` elapsed scale for baseline and current, rounded from the longer recorded duration. Any future side-by-side detailed Timeline comparison must reuse the same shared-scale owner and synchronized range; if baseline wall-clock Step timing is unavailable, the browser must not invent it.
