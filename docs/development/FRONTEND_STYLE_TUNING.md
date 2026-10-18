# Frontend style tuning

> **Development tuning reference.** The checks mentioned here are focused diagnostics after UI changes; they do not replace or redefine the canonical APP Plan in `APP_TEST_RUN.md`.


Oh-Goon keeps one browser visual identity: **dark rink + snow/ice + restrained cyan**. The theme is intentionally adjustable, but it is not a selectable-theme system.

## One tuning owner

Change visual values in `frontend/src/app/theme/themeTokens.ts`. Authored CSS must consume the generated semantic CSS variables; do not add raw colors or feature-local palettes.

Useful knobs:

- `ice.canvasBackground` — overall snow/ice atmosphere and glow.
- `ice.panelBackground` / `ice.subtleBackground` — panel frost and depth.
- `ice.navigationBackground` — top/bottom navigation glass.
- `ice.frostLine`, `ice.rinkLine`, `ice.snowSheen` — decorative ice/rink intensity.
- `action.*` — primary cyan action treatment.
- `status.*` — semantic success/info/warning/danger colors; do not repurpose these as decoration.
- `radius.*` — shape personality.
- `space.*` and `layout.panelPadding` — density.
- `layout.controlHeight` — touch/control sizing.
- `shadow.*` — depth and focus visibility.


Game Tape status graphics also share one dynamic theme-owned trio: `--gt-status`, `--gt-status-bg` and `--gt-status-border`. They may be reassigned only from exact canonical `data-status` values; use them for new Flow/Timeline status visuals instead of writing another success/danger/warning/info palette.

The CI/CD Flow intentionally uses the same ice tokens for rink-like lanes, connectors and selected-Step focus. Keep the chart visually distinctive through surfaces, borders, spacing and semantic status accents; do not hide long Plan/Goon/Step names to make the diagram compact. Desktop may scroll each repository track locally; mobile must reflow the sequence vertically.

## Product rules

1. Keep text and actions readable before decoration.
2. Standard mode leads with human names and the next useful action.
3. Technical IDs, hashes, receipts and exact contracts remain accessible through explicit details/disclosures.
4. Advanced mode changes reachability and density, never backend authority.
5. Mobile and desktop expose the same product concepts; layout may differ.
6. Green, amber and red are status semantics. Do not use them as brand accents.
7. Hockey language may add personality, but it must not replace precise product labels such as Repository, Shared Run, Schedule, Run, Plan, Goon, Step or Game Tape.

## Safe visual experiments

Good experiments are token-only changes: slightly brighter snow glow, darker panels, more/less frosted navigation, tighter spacing, smaller/larger radius, or stronger cyan focus/action emphasis. Run `make frontend-check` after any theme change; it includes the source contrast floor, static i18n parity, lint, component tests, coverage and the production build. The source contrast floor is 4.5:1 for the required text/action/status token pairs; focused axe/Playwright diagnostics verify rendered browser combinations when browser engines are available. The canonical APP browser execution remains owned by `oh-goon/app-certification`.

Search current TS/TSX ownership and remove selectors that belong only to superseded browser surfaces. Visual iteration should simplify or reuse current primitives rather than accumulate compatibility styling; an arbitrary total-byte threshold is not a substitute for this review.

Do not create `darkTheme.ts`, `iceTheme.ts`, per-feature color maps, CSS compatibility aliases, or runtime theme state merely to test a look. If a visual experiment is rejected, revert the token values; the product DOM and semantics should not need a migration.
