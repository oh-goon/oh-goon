# Browser Product Coverage

901.0.0 keeps the 900 backend ownership model and turns browser parity into a human-first default experience. Standard users should encounter product concepts and next actions first; hashes, queues, raw source and executor authority stay available through progressive disclosure or Advanced experience.

> **Historical browser-evolution record.** Parenthesized numbers such as `(910)`–`(938)` below are historical feature-sequence labels, not current or future Oh-Goon checkpoint assignments. Use the [frontend owner guide](../../frontend/README.md) and the current source/tests for present behavior; archive-local checkpoint status is not required in a GitHub checkout.

900.0.0 established the browser as a complete management and review surface over existing backend authority. The browser may collect user intent and present backend facts; it must not resolve Plans, infer admission, reconstruct execution semantics, or create a second scheduling/run model.

## Product surfaces

| Product owner | Browser responsibility | Backend authority |
| --- | --- | --- |
| Repositories | Discover/manage League repositories, include/exclude, inspect readiness, start repository-owned CI/CD, open the resulting Run | repository discovery, repository-owned Plan/Goon resolution, admission, Fleet Run |
| Shared Runs | Choose one saved Shared Plan, choose explicit repositories/Lineup, Check, Run, review previous runs | Shared Plan parsing/resolution, readiness, exact repository scope, Fleet Run |
| Shared Plans | Create from Plan templates, inspect/edit/save source, show revision/change state and run history | Plan parser/resolver, template catalog, source SHA/revision, history |
| Schedules | Create from repository + saved Shared Plan + interval, edit name/interval, pause/enable, Run now, delete, open Game Tape | backend resolves and freezes the Plan/source; Schedule owns explicit targets + one immutable Plan + wall clock; repository Runs are paged independently |
| Runs / Game Tape | Browse executions, inspect timeline/evidence/failures, navigate to safe recovery actions | canonical Fleet Run, Enforcer/evidence, recovery/readmission semantics |
| Advanced Operations | Inspect executor pool/placement queue; activate/drain; trust revoke/restore; cancel queued admission | executor capacity, placement, trust, operational revisions and queue transitions |

## API coverage rule

Authored `ApiClient` bindings exist when a browser product workflow needs the operation. Raw/download or lower-level projection endpoints do not need a second ergonomic binding merely to reach numerical parity. In 900.0.0 the intentionally direct/lower-level cases include the raw Enforcer YAML download and source/catalog projections already represented by higher-level browser models.

## Backend-change stopping rule

Do not start another broad backend architecture phase for browser convenience. Change the backend only when a real browser workflow would otherwise have to invent backend semantics, duplicate an authoritative fact, or assemble an unsafe low-level request.

The Schedule creation path is the reference example: React submits explicit `repository_ids`, `plan_key`, interval/name and explicit inputs. `SharedPlanScheduleService` resolves the saved Plan in Python, binds its exact SHA/source, and persists the immutable Schedule definition with that canonical repository target set. React never constructs `ResolvedFleetPlan` or repository-run fan-out semantics.

## Review questions

Before adding frontend state or a new endpoint, ask:

1. Is this merely presentation/navigation over an existing backend fact? Keep it frontend-only.
2. Would React have to parse, resolve, validate, infer admission, derive identity, or freeze execution authority? Add or reshape a narrow backend intent operation instead.
3. Does this create another Run, Schedule, Plan, repository, or evidence identity? Stop and use the existing owner.
4. Is the feature ordinary management or operator authority? Keep executor/trust/placement controls in Advanced Operations rather than primary CI/CD flows.

## 901 usability hierarchy

The standard browser journey is intentionally short:

1. **Repositories** — see managed Teams and run repository-owned CI/CD.
2. **Shared Runs** — choose Plan, choose Teams, Check, Run.
3. **Schedules** — choose repository, Plan and familiar frequency; manage future runs.
4. **Runs** — open the latest execution and review Steps, Overview, Timeline and Logs. The Steps view contains the read-only CI/CD Flow visualization/finder.

Settings and Help are utilities rather than workflow destinations. Recovery remains reachable from canonical failure guidance, and Recovery/Tools become top-level navigation only in Advanced experience. Technical identities remain inspectable but do not lead the standard workflow.

### Usability stopping rule

Do not make a backend change to solve labeling, ordering, navigation, filtering, disclosure, responsive layout or other presentation problems. Escalate only when a browser workflow would otherwise have to invent or duplicate authoritative backend semantics.
## Human-first review rule (historical feature 903)

Everyday browser surfaces lead with names, outcomes and next actions. Exact IDs, hashes, source contracts, execution receipts and environment fingerprints remain available but progressively disclosed. Shared Runs uses compact Game Tape; Runs owns the complete review surface. Presentation search/filtering never changes backend repository, Plan, Schedule or run scope.
## State-clarity rule (historical feature 904)

Loading is not empty. An active run is shown before controls it temporarily blocks. Repository management lives on Repositories, with Settings linking to that owner instead of duplicating the Team map. Help follows the current Repositories → Shared Runs → Schedules → Runs journey. These are frontend presentation rules and never authorize React to infer missing backend facts.
## Guided-action rule (historical feature 905)

Everyday workflows expose the next useful action in human language. Shared Runs renders its existing backend-derived setup state visibly; first-run guidance follows Repositories → Shared Runs/Schedules → Runs; live runs can always open the canonical Runs surface; destructive live-run cancellation is confirmed in the browser. These presentation safeguards never create a second readiness, locking or cancellation model.
## Consistency and action-feedback rule (historical feature 906)

Create and edit surfaces use the same human vocabulary for the same backend fact. Schedule frequency presets are a lossless presentation over exact `interval_minutes`, with raw minutes exposed only through Custom. Saved writes are acknowledged without claiming execution completion, and disabled everyday run actions state their immediate blocker. These rules remain frontend presentation over existing backend causality/run state.
## Current-owner context rule (historical feature 907)

Standard pages lead with the current product owner: Repositories leads with Team CI/CD rather than maintenance-update summary; Shared Runs surfaces changed selected-Plan state before execution; Runs and Game Tape use one human run label while exact Fleet Run IDs remain technical/search identity. Superseded projections are deleted when they no longer have a current product owner; progressive disclosure is reserved for current information that is useful but not part of the first-read path.


## First-use and CSS-headroom rule (historical feature 908)

First-use dialogs must remain visibly usable while reads are unresolved or refreshing, explain resolved empty states, and restore keyboard focus when dismissed. Styling from superseded browser surfaces is removed once it has no current source/architecture owner; do not raise the CSS budget merely to retain compatibility selectors. Folder guidance remains presentation over backend filesystem discovery and never infers repository eligibility in React.
## Executable browser-quality rule (historical feature 909)

Every normal browser capability must have a real-browser journey or explicit route-quality check. Content Visibility and Theme Integrity are separate hard gates alongside accessibility: critical operational information cannot be silently clipped/truncated or obscured at supported viewports/reflow pressure, and accessibility changes must retain the token-owned snow/ice/hockey identity. Browser fixtures make existing backend facts deterministic but never create a second execution, Plan, Schedule, recovery or evidence model.
## CI/CD Flow review rule (historical feature 910)

Game Tape opens on the primary **Steps** review tab. That tab contains the **CI/CD Flow** visualization, a read-only projection of the same admitted sequential execution already used by Timeline, Logs and the Step inspector: Plan → repository lane → recorded Goon/source groups → ordered Steps → visible outcome. Goon labels come only from recorded Step source provenance (`goon.yml`); missing provenance is shown as resolved Plan Steps rather than guessed. Arrows communicate admitted order and never create DAG/scheduler semantics.
## Live CI/CD Flow review rule (historical feature 911)

The CI/CD Flow inside the Steps tab remains a read-only projection but may make large executions faster to review: recorded Step-status counts, repository-lane navigation, explicit repository outcome nodes, selected-Step lane context and lane-local review focus all reuse existing Game Tape identity/status. Review focus never changes admitted order or backend failure meaning. Multi-repository fixtures must use distinct canonical Step execution identities, and all lane/source/Step text remains under Content Visibility and reflow gates.
## Non-destructive Flow finder rule (historical feature 912)

Large CI/CD Flow diagrams may offer local find/navigation across repository, recorded Goon/source and Step text. Matches are highlighted and may be selected explicitly, but non-matching admitted work is never filtered, collapsed or removed. Finder result identities are canonical repository/Step execution IDs already present in Game Tape, so search cannot become a second run/evidence model.
## Selected-Step orientation rule (historical feature 913)

An explicitly selected Flow Step may expose Repository → recorded Goon/source → Step context, lane position and Previous/Next review actions. Adjacency is strictly the selected repository execution's existing Step array and stops at lane boundaries; no cross-repository ordering is inferred. Finder, review focus, direct clicks and Previous/Next all reuse canonical Step selection/inspection.
## Factual Flow-signal rule (historical feature 914)

CI/CD Flow may summarize recorded Step statuses per contiguous Goon/source segment and display canonical bottleneck/attempt facts, but it cannot infer critical paths, Goon health, retry policy or execution priority. Motion is decorative only, reduced-motion-safe, and never replaces text status.


## Canonical failure recurrence rule (historical feature 967)

A selected failed Step may answer “Seen before” from the existing canonical failure-history contract before the reviewer opens Logs. The compact inspector summary is restricted to exact occurrence/first-seen/last-seen/recurrence-after-pass facts. Full sequence, context and occurrence evidence remains in Logs; frontend log parsing or fuzzy failure grouping is forbidden.


## Bounded large-log rule (historical feature 968)

Raw stdout/stderr review must keep browser DOM work bounded even when the backend retains a much larger bounded chunk set. Search/stream filters apply to all loaded chunks for the selected Step, while at most 120 matching chunks are rendered simultaneously. Earlier/Newer navigation keeps all loaded matches reviewable, and canonical failure navigation must open the render window containing its recorded sequence before focus.


## Step-local artifact rule (historical feature 969)

Artifacts stay attached to the selected canonical Step. PRESENT/MISSING state and concise retained evidence are visible before technical detail; full digest/retained-object identity is disclosed on demand. Browser preview must not be inferred from filename because current Game Tape evidence does not provide artifact bytes or MIME type.

## Typed Run-review-state rule (historical feature 970)

Runs/Game Tape durable browser state must round-trip through the single typed `RunReviewState` owner. Adding a new URL review concern directly in `RunsPage` is a regression: existing Run/Step/view/comparison/history/Logs/Flow/disclosure state must survive unrelated patches and share-link serialization without changing backend semantics.

## Durable bounded log-window rule (historical feature 971)

Raw-log review may restore `log_page` together with the existing query/stream/disclosure state, but the persisted value addresses only the 120-chunk render window. Search/stream/Step changes reset it, stale values clamp after loaded data exists, and failure navigation may replace it with the window containing the canonical recorded failure sequence.

## Contextual artifact-action rule (historical feature 972)

Step-local artifact review may offer Copy SHA-256 and Copy retained reference directly beside recorded artifact evidence. Actions serialize only backend-recorded identities already visible in Game Tape; they do not imply browser content access, download authority, MIME knowledge or a detached artifact catalog. Missing artifacts remain explicit and never receive copy actions for facts that do not exist.

## Previous exact-occurrence comparison rule (historical feature 973)

A selected failed Step may offer **Compare previous exact occurrence** only when canonical failure history supplies a nearest prior exact-signature occurrence and that exact Run is already available in the current backend-derived comparison option set. The browser must not search farther back for a more convenient baseline, infer comparability, or manufacture a Run Diff from failure prose.

