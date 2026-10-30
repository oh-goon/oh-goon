# Frontend agent guide

Read root `../AGENTS.md` first for repository-wide rules, then `README.md` for the frontend owner map and `src/README.md` when choosing between app/domain/feature implementation owners. This file adds only frontend-local ownership details.

`frontend/` owns React/TypeScript presentation, interaction, local display state, API invocation, and frontend test infrastructure.

## Work here when

- changing screens, components, layout, or accessibility;
- changing frontend read/mutation lifecycle presentation;
- changing browser-only interaction or harmless local preferences;
- consuming an already-defined API contract;
- adding frontend tests.

## Do not implement here

- execution/admission semantics;
- repository safety/isolation rules;
- Goon/Plan interpretation;
- backend validation/policy meaning;
- API method/path/request/response identity;
- generated models/validators.

Those are backend/generated authorities.

## Contract imports

```text
@generated/* → ../generated/frontend/*
@test/*      → tests/support/*
@/*          → authored frontend source
```

Never restore `src/domain/api/generated/` and never hand edit `generated/frontend/`.
React should render backend-owned semantic facts rather than reconstruct them.

## Tests

Behavior tests stay colocated beside their `app/`, `domain/`, `components/`, or `features/<feature>/` owner.
`frontend/tests/` is support-only; shared frontend test harnesses/fakes belong in `frontend/tests/support/`.
`frontend/e2e/` owns Playwright user journeys, browser accessibility, content visibility/reflow and focused theme regression; read `e2e/README.md` before extending it.


CI/CD Flow is a primary Game Tape review surface. Preserve **Plan → repository lane → recorded Goon/source → ordered Step → outcome** and reuse canonical `step_execution_id` for selection/inspection. Lane navigation, status counts, review focus, Flow search and selected-Step Previous/Next may only project recorded Game Tape identity/status/text; review focus and adjacency stay repository-lane-local, and search must highlight/navigate without filtering or changing order/backend meaning. Never infer a Goon when provenance is absent, never reorder/group non-contiguous Steps, and never turn the flowchart into an editable graph, DAG scheduler, or frontend execution model.

Never sacrifice information for layout. Critical operational text must wrap, scroll locally, or remain available through an explicit disclosure; do not silently clip/truncate it. Failure focus is explicit presentation state only: it may quiet Passed graphics after user action, but must never filter, reorder, auto-hide or redefine canonical Step status. Failure navigation must use canonical failed/interrupt Step identities; Copy error may serialize recorded reason/failure fingerprint facts but must not parse raw logs or invent a severity. Compare previous exact occurrence may use only the nearest canonical failure-history occurrence and only when that Run is already present in backend-derived comparison options; never skip to an older occurrence or synthesize comparison eligibility. Step inspector must remain progressive: explanation/status/failure actions first; exact identity/command/environment/outputs stay available under explicit technical disclosure. Accessibility changes must preserve the token-owned snow/ice/hockey identity rather than adding a generic replacement theme. Timeline charts are also truth-preserving: fixed canvas, explicit scale, exact proportional bars, and one shared elapsed domain for visual Run comparisons; never independently auto-fit compared Runs. Raw-log review is likewise bounded presentation: search/filter the full loaded canonical Step output, render at most 120 matching chunks at once, and keep all loaded matches reachable through explicit Earlier/Newer navigation; never solve browser DOM growth by redefining backend retention. Artifact evidence must stay Step-local: reuse the shared artifact owner, expose only recorded PRESENT/MISSING/hash/retention facts, and never infer MIME/content preview from a filename when backend evidence does not provide bytes/type. Contextual artifact actions may copy only canonical recorded identities such as full SHA-256 or retained reference ID; they must not invent preview/download semantics or detach artifacts from the producing Step.

## Commands

From `frontend/`, the ordinary local loop is:

```text
npm run test -- src/<affected>/<component>.test.tsx
make -C .. check
```

If the API contract changed, run `make -C .. generate` and `make -C .. check-generated`; never edit generated files directly.

Use the smallest real browser proof when the changed behavior needs one.
Complete frontend and browser portfolios remain daily/release qualification;
`npm run check` is available explicitly, not mandatory for every local edit.

For a fresh or existing checkout, root `make init` delegates locked frontend materialization directly to native `npm ci`. Browser engines are release/focused-journey dependencies and remain separate; run `make -C .. browser-setup` when they are needed. The dependency-independent ice-theme contrast floor is part of `make -C .. frontend-check`; the complete release browser matrix plus reviewed Chromium screenshots is `make -C .. frontend-browser-release`.

## Frontend boundaries

- Dependency Cruiser owns frontend import layering. Features use another feature only through its public root; do not duplicate those import rules in another architecture scanner.
- Keep raw HTTP transport and mutation/read lifecycle ownership centralized in the existing app API owners.
- Do not create a second API metadata table.
- Do not create generic `shared/` or `utils/` dumping grounds.
- Keep Plan → Goon → Run UX backend-driven: consume Goon maturity, admission blockers, resolved identity and evidence facts; never infer them from category names or duplicate backend policy. Roster/readiness/execution stay primary while YAML, resolution traces and receipts stay progressively disclosed.
- Own empty/loading/blocked/ready/terminal presentation explicitly: announce important state changes, expose current steps/selections semantically, move focus only after an explicit user check, and keep focused React regressions beside the component.
- Keep Game Tape read-only during failure recovery: consume canonical failure codes for guidance/navigation only; Runs owns exact-target rerun and placement readmission, and Recovery owns uncertain/partial repository effects.
- Keep first-run setup state-free and truthful: adding a League folder only persists/scans the selected parent path; show folder loading/error state immediately and teach the same Choose Plan → Choose Teams → Check → Run flow used after setup.
- Keep large-League filtering presentation-only: Run Setup owns the single selected Team-ID list; search/Selected-only may hide Teams but never mutate scope, visible-only bulk actions stay explicit, and selected/visible/total counts keep hidden selections obvious.
- Keep Shared Runs and Schedules visibly distinct: Shared Runs are operator-triggered; a Schedule is time-triggered over an explicit repository target set + one Plan. Both converge on the same per-repository Run/Game Tape investigation path.
- Schedule execution links to the canonical Fleet Run/Game Tape; do not create Schedule-specific logs, failure interpretation, or debugging state in React.

Finish with the six-field **Completion evidence** report defined by root `AGENTS.md`; incomplete or timed-out checks stay `unverified`, never `passed`.

### Runs review ownership

- `app/pages/runReviewState.ts` + `RunsPage` own durable browser review state for Run History and Game Tape. Game Tape, Flow, Timeline, Logs/Evidence and the Step inspector are controlled consumers; do not create a second durable review-state mirror.
- Backend Run/Step/evidence identity remains canonical. URL state is presentation only and must be normalized against the loaded Run before use.
- Local component state is appropriate only for ephemeral mechanics such as focus restoration, copy feedback, paging windows and live-follow.
- Read lifecycle ownership is likewise singular: `READ_RESOURCES` owns scope/resource mapping, `ReadController` owns concurrency/owner leases, `useReadResource` owns mounted refresh, and mutation reporting owns invalidation. Do not manually refresh the same read after a saved mutation unless the user explicitly pressed a Refresh/Retry control.
- In Playwright, start Run-detail assertions from the existing `Game Tape` owner and Flow-specific assertions from `CI/CD execution flowchart`; use ordered `.first()` only after entering the correct semantic owner.
- Browser local-access bootstrap is transport-only: `app/api/httpTransport.ts` owns the physical request, `app/api/localAccess.ts` exchanges the launch fragment for the HttpOnly API cookie, and product state must never persist or mirror the capability.
