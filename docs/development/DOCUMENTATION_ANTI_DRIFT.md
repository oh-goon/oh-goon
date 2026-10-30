# OH-GOON — Permanent Documentation Anti-Drift Rules

## Status

**NORMATIVE DOCUMENTATION REQUIREMENTS FOR THE 1267.0.1053 AUTHORITY-SIMPLIFICATION CAMPAIGN AND FUTURE WORK.**

This file does not implement runtime behavior. It defines how architectural decisions must be preserved in active documentation so future work does not reintroduce removed cucumber.

Internal Plan/Goon names are provenance unless their exact identity is an explicitly documented semantic/public contract. Do not document or reintroduce historical-name dispatch for scheduling, blocking, repair advice, capability, admission, or execution. A behavior-preserving internal rename must preserve those semantics; when an identity-only branch is deleted, prefer less machinery rather than a replacement allowlist/classifier registry.

Before release, Oh-Goon has **no internal backward-compatibility requirement**. Obsolete internal APIs, historical journal/event shapes, deprecated aliases, migration shims, fallback keys, internal Plan/Goon identities, and architecture/test checks that exist only to preserve an earlier development generation are deleted rather than deprecated. Compatibility exists only where a current external/public contract explicitly requires it. Unknown obsolete input fails as unknown/invalid; the repository does not carry transitional readers merely because an older development checkpoint emitted the old shape.

## Permanent rule

> **When an architecture cleanup establishes a new invariant, the active normative documentation must be strengthened in the same campaign. A green implementation without aligned current documentation is incomplete.**

The documentation must prevent future work from reconstructing a superseded mechanism merely because an older file still describes it.

## Authority invariants that must remain explicit

- The valid path is the simplest path.
- Resolve once, transport once, execute once, report once.
- Normal CLI workflows use `init`, `workflow inspect|validate|prepare`, `run`,
  `runs` and `doctor`; app lifecycle lives under `app`. Authored selections use
  v2 `target.repository`, resolved from the exact selection file's directory.
  Superseded public commands and v1 live selection files fail before effects;
  retained Run/recovery facts still use their original durable owners.
  Init explicitly registers only the selected exact Git root when needed and
  publishes the complete workflow with its first canonical lock. Incomplete
  authoring requires `--draft` and remains NOT_READY. Read operations are local
  and read-only unless acquisition is explicit; Run never registers, refreshes
  refs or rewrites a lock. Do not restore Plan-path/input-file/agent flag assembly,
  compatibility parsers or conversion routes. Plans/Goons retain executable
  meaning, the original binder retains types/defaults, native providers retain
  settings, and fresh admission retains effect permission.
- `make init` is the single normal local dependency-materialization boundary.
- `make certify` consumes initialized authority; it does not recreate environments, choose another interpreter, rerun `uv`/`npm` materialization, or reacquire already-present locked dependencies merely to prove APP behavior.
- APP/self verification does not manufacture or inherit strict release/CI executor authority. The outer certification frontdoor must strip `OH_GOON_EXECUTORS_FILE`, certification cgroup overrides, and certification systemd-delegation markers before static admission or any APP bootstrap/orchestration work; inner bootstrap repeats that stripping as defense-in-depth. No APP layer **or APP Goon** may discover, validate, materialize, export, or require strict executor/cgroup authority for a verification Plan that does not consume strict placement. The APP local-runtime Goon is functional start/auth-proxy/stop verification only; strict repository-Goon/cancel/crash/restart acceptance remains a separate explicit owner.
- Fresh-bootstrap, packaging, and reproducibility proofs are separate authority surfaces and may intentionally materialize from clean source.
- Requirement discovery is not permission authority. Candidate-controlled source may reveal a need but cannot grant itself a new capability.
- Ordinary repository-native/package-local tools do not require redundant Oh-Goon declarations.
- Explicit capability declarations remain only when they add independent permission information.
- Shipped Plan templates do not own fixed execution resource caps. APP certification has no privileged `execution.resources` exception; omitted CPU/memory/PID caps inherit the selected executor's admitted capacity, while any explicit portable/user-authored cap remains machine-owned execution/admission policy.
- Authored-source membership has one canonical owner. Template discovery and template-shape validation must agree that known transport sidecars such as Windows/WSL `:Zone.Identifier` siblings and applied `*.patch` transport files are not authored template members; no validator may bypass that policy with raw directory membership checks. Physical source admission still fails closed while those artifacts exist, and explicit `oh-goon-source-layout transport-clean` remains the sole cleanup owner.
- Repair/recovery transport never becomes source by incidental placement. Recovery stores, continuation inputs, generated handoff projections, patches, archives, evidence bundles, reports, logs, restore destinations, and OS/editor sidecars stay outside the checkout they protect. Filesystem recovery tooling must fail before mutation when an owned transport path is inside canonical source; do not compensate with ignore rules or silent init/certify/build cleanup.
- Frontend API fixtures and test doubles must preserve the same freshness/authority identities as the real generated `ApiClient` contract. When a write gains a reviewed identity such as `execution_digest`, fixture previews must expose that identity and fixture writes must validate it before mutation; tests must not keep an older, weaker signature merely because browser fixtures are synthetic.
- Source shape, line count, branch count, formatting, and similar maintainability proxies are not APP certification authority.
- Source positions may be evidence metadata where genuinely relevant, but do not become unrelated APP product identity.
- Known-invalid authority fails at its earliest legitimate owner before workload begins.
- Admission-before-work is purpose-independent and has one base owner. Any effect-free repository+Plan fact that can invalidate execution must be validated by `FleetAdmissionPlanner` before durable `RUN_ADMITTED`, attempt claim, immutable-input preparation, or Goon/Step execution. Strict CI/CD may add stronger proofs, but verification/inspection/maintenance/migration may not defer the same base answer to a different owner. Runtime preparation may revalidate admitted facts for drift, but may not be their first validator.
- Admission must not become workload simulation. A requested test, verifier, or command may legitimately return a failing result after admission; only facts that make execution impermissible or insufficiently specified belong to pre-work admission.
- Runtime-only failures remain owned by the native runtime; do not add speculative preflight machinery.
- Native tools remain native. Do not add wrappers, shims, second resolvers, PATH simulators, or proxy package managers merely to satisfy Oh-Goon.
- Release version stamping is version authoring, not dependency authoring. Native uv owns the backend project-version mutation through `uv version --frozen`; committed `backend/uv.lock` must remain byte-identical. Do not document or reintroduce `uv lock --check`, lock reconciliation, hand-authored `uv.lock` root metadata, or an Oh-Goon lock-convergence substitute in the release-version transition.
- Normal cancellation must release owned state and allow immediate subsequent valid execution without ritual reset.
- Tactical containment must be labeled with its reason, target owner, removal condition, and removal phase.

## Documentation precedence

Current normative architecture documents outrank stale historical implementation descriptions.

When a historical document still describes a superseded model, update it or mark that section clearly historical/non-authoritative. Do not leave two current-looking contradictory instructions.

## Required active-document sweep

Every architecture campaign that changes authority must review at least:

```text
AGENTS.md
FAIL_BEFORE_WORK.md
ARCHITECTURE_DECISIONS.md
APP_CERTIFICATION.md
NATIVE_TOOL_AUTHORITY.md
SOURCE_MAINTAINABILITY_POLICY.md
STARTUP_SIMPLIFICATION_PHASES.md
STRUCTURAL_AUTHORITY_GUARD.md
current phase/handoff documents
```

The exact filenames may evolve. The requirement is to update the current owners of these concepts, not to preserve filenames forever.

## Anti-drift review questions

Before closing a campaign, ask:

```text
Does any active doc still say certification owns dependency materialization after init?
Does any active doc still imply certification should download already-materialized packages?
Does any active doc allow a second interpreter selection during ordinary APP certification?
Does any active doc give APP certification or another Plan template a privileged execution.resources override instead of leaving resource authority with the backend execution/admission owner?
Does any shipped Plan README or active normative doc publish fixed CPU/memory/PID values as effective policy even though omitted caps inherit the selected executor's admitted capacity?
Does any active doc or validator define authored-source membership independently from the canonical source-transport policy, including by treating `:Zone.Identifier` or applied `*.patch` transport files as template content?
Does any recovery, repair, evidence, or debugging instruction stage its own patch/store/input/handoff/archive/report/log/restore destination inside canonical source, or rely on a later ignore/cleanup step to undo that self-contamination?
Does any frontend fixture, fake client, or test double implement a weaker freshness contract than the generated `ApiClient`, for example by omitting an execution digest or other reviewed identity from preview/write coupling?
Does any active doc make a source-shape proxy authoritative for APP certification?
Does any active doc tell authors to add declarations for ordinary repository-native tools?
Does any active doc blur requirement discovery into permission authority?
Does any active doc require `make strict-executor-setup`, systemd/cgroup repair, or another preflight ritual before normal make certify?
Does any active doc make APP/self verification depend on, preserve, or inherit strict release/CI executor inventory, systemd delegation, or cgroup capacity that it does not consume?
Does any active doc recommend wrappers/shims/second resolvers around native tools?
Does any active doc describe release stamping as dependency/lock reconciliation instead of native `uv version --frozen` with byte-identical committed `backend/uv.lock`?
Does any active doc require the complete native WSL/browser/certification/release ladder after each small mutation instead of risk-selected focused/group gates followed by one exact-source final closure?
Does any active doc allow physical execution to be enabled before native containment/teardown/credential gates merely because full release qualification is scheduled later?
Does any active doc reclassify BLOCKED/NOT_RUN/NOT_AFFECTED as PASS or carry a release PASS across changed source bytes?
Does any active doc make private Hashmarks filenames/class/helper names or frontend local-variable spelling architecture authority?
Does any active doc make private workflow local/helper spelling architecture authority instead of proving semantic lock transport?
Does any active doc infer component ownership from filename prefixes such as `scout_*` rather than an explicit package boundary?
Does any active doc allow ancestor directory names beneath a configured League root to hide otherwise valid Git-root Teams?
Does any active doc grant broad `.hashmarks-*` names transient-source authority instead of limiting the exclusion to exact current `.hashmarks/` state?
Does any active doc tell Oh-Goon to vendor, reacquire, repackage, or privately materialize Hashmarks instead of selecting it through pyproject.toml + uv.lock?
Does any active doc copy the current Hashmarks release/version/commit/tree/wheel as a second compatibility authority instead of binding evidence to the loaded producer identity?
Does any active doc require package-local `node_modules/.bin` readiness probes before native npm scripts, or duplicate `make init` without a new isolated subject?
Does any execution path create `RUN_ADMITTED`, claim an attempt, prepare immutable inputs, or execute a Goon before all effect-free prerequisites that can invalidate that run have passed, or use runtime revalidation as the first validation?
Does any execution purpose select a different owner for the same base repository+Plan admissibility question instead of letting strict modes add proofs to one shared owner?
Does any active doc turn expected workload outcomes such as test failures into shadow admission policy?
Does any active doc present tactical containment as the target architecture?
Does any active doc preserve a historical five-phase APP model solely because dependency-materialize used to be a Goon?
Does any internal API, event reader, alias, migration shim, fallback key, architecture grep, or test remain solely to accept or preserve a superseded development-generation shape?
```

Any `yes` is documentation debt to resolve before the campaign is considered complete.

## Important non-goal

Do not turn documentation anti-drift into another brittle APP certification gate based on exact wording, line numbers, or document shape.

The guard is semantic: active docs must not contradict the current architecture.

Documentation validation belongs to review/development governance, not runtime product authority.

## Definition of done

The documentation side of an architecture campaign is complete when a future engineer or coding agent can read only the current active docs and recover the intended authority model without relying on chat memory, hidden context, or historical archaeology.
### Source authority transport

Does any path hide repository source identity inside a preview/selection hash, infer source
authority from strict-only execution evidence, or rediscover source identity downstream after
admission? Repository subject identity and filtered source snapshot must be explicit admission
facts transported through execution, evidence, certification, and packaging.
Does any success/package path branch on direct versus strict execution to decide which source
passed? That is duplicate authority: both modes must consume the same durable repository
admission source authority. Strict-only evidence can prove execution mechanics, not source
identity ownership.

