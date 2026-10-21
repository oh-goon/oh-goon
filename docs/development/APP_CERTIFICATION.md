# APP certification

`make certify` is the final release-critical Oh-Goon verification authority for one exact, already-initialized checkout. It is intentionally smaller than `make verify`.

## Permanent startup/materialization boundary

Normal local use has one dependency materialization owner:

```text
make init
    -> native uv locked backend materialization
    -> native npm committed-lock frontend materialization
    -> executable smoke

make certify
    -> consume that initialized repository authority
    -> do not materialize dependencies again
```

`make certify` must not create a second `.venv`, select another Python interpreter, rerun `uv sync`, rerun `npm ci`, or contact package registries merely to reacquire dependencies already established by `make init`. Fresh-source/bootstrap/release reproducibility is a separate proof surface.

If required initialization is absent, the responsible frontdoor must fail before workload with clear `make init` guidance. Certification must not silently initialize. Failure guidance must not ask the operator to repeat a successful prerequisite because an internal layer discarded its authority.

Normal certification consumes canonical source authority, not Git authority. Git metadata is optional and must never be manufactured merely to make self-certification or release packaging admissible. When Git is present because a caller/repository genuinely owns it, Git remains native authority for explicitly Git-derived facts only; it is not a prerequisite for Oh-Goon's own source identity, APP certification, or package production.

Native uv compatibility has one owner: the repository's `[tool.uv].required-version` declaration. Readiness may invoke a project-bound native uv operation and preserve its result, while diagnostics may report the observed installed version; Oh-Goon must not maintain a second minimum-version comparator or policy variable.

The normal frontdoor proves only the prerequisite it directly owns: the initialized backend runner required to enter certification. It must not invent a second "init-complete" database, cache receipt, or shallow `node_modules` presence check. Once the runner exists, each semantic verification owner reports its own genuine runtime failure; none may reacquire dependencies as repair.

## What `make certify` proves

`templates/plans/oh-goon/app-certification/plan.yml` is the sole APP ordering owner and runs with `execution.purpose: verification` against the admitted initialized repository. Its semantic Goons are:

| # | Goon | Canonical command | Purpose |
|---:|---|---|---|
| 01 | `oh-goon/backend-p0` | `make verify-p0` | execution/security authority and generated-contract freshness |
| 02 | `oh-goon/architecture` | `make architecture` | semantic repository ownership/authority boundaries |
| 03 | `oh-goon/template-startability` | `make template-startability-certification` | exact shipped Plan catalog resolution, canonical fixture binding, production admission through `RUN_ADMITTED`, and zero Step work |
| 04 | `oh-goon/frontend-functional` | `make frontend-functional-certification` | frontend functional readiness |
| 05 | `oh-goon/local-runtime` | `make local-runtime-verification` | deterministic start/probe/stop of the local product runtime without strict Fleet executor authority |

The number five is not itself product authority. The invariant is that dependency acquisition/materialization is not an APP workload after canonical initialization already succeeded. Template startability may materialize only the bounded canonical certification fixtures needed to prove their declared repository contracts; it must not recreate or reacquire the initialized Oh-Goon backend/frontend environment.

Each selected fixture environment is materialized once by native `uv sync --locked`
using the execution subject's selected cache and native network configuration. A
subsequent `uv sync --check --locked --offline` verifies that same environment
without repair or dependency acquisition, and the harness preserves the fixture
lock bytes. An empty attempt-private cache is valid setup input, not evidence that
the fixture dependencies are unavailable. Explicit native offline configuration
still applies to setup; a cache miss then remains a native failure without fallback.

The strict CI/CD Fleet executor remains the authority for CI/CD workload isolation and frozen executable capability. APP self-certification is verification of this already-initialized local product; it must not manufacture, inherit, validate, export, or require strict executor/cgroup/systemd-delegation authority that the verification Plan does not consume. The outer certification frontdoor classifies APP verification and strips that authority before static admission or any APP bootstrap/orchestration work; the workflow bootstrap repeats the stripping as defense-in-depth; and the APP local-runtime Goon uses a verification-only runtime journey that never reacquires it. Production-shaped repository Goon execution, physical Step cancellation, crash/restart recovery, and strict parallel Shared Plan execution remain under `make strict-local-runtime-acceptance`, `make test-real-world`, and `make test-real-world-parallel`. `make strict-executor-setup` belongs only to explicit strict release/CI or strict-acceptance paths.

## Certification semantic boundary

APP certification proves product/runtime semantics. Source line counts, function length, branch/nesting scores, formatting, harmless source movement, documentation shape, and unrelated source-position metadata are not APP authority. Exact source ranges may remain valid evidence inside Repository Understanding where source location is explicitly the evidence contract; they must not leak into unrelated APP validity.

Release/dependency lock validity belongs to release/package/dependency authority, not generic architecture merely because native `uv` or `npm` can check it. `oh-goon/architecture` therefore must not acquire ordinary package-manager tools solely to perform release-lock validation.

Known-invalid pre-execution state fails at its earliest legitimate owner. Runtime-only failures remain native runtime failures. Requirement discovery never grants permission: candidate-controlled source may reveal a tool need, but independently owned permission authority decides whether it may execute.

## Single decision rule

`make certify` performs canonical static admission once, through the certification frontdoor, before mutable certification state or workload. Make is a thin human entrypoint; repeating the same admission in Make and again in the frontdoor is not additional safety.

The governing rules are `FAIL_BEFORE_WORK.md`, `ARCHITECTURE_DECISIONS.md`, and `NATIVE_TOOL_AUTHORITY.md`.

## What stays outside APP certification

Broad backend/tooling/integration/P1/browser diagnostics remain under `make verify`. Fresh dependency acquisition/reproducibility belongs to initialization/release/package qualification. Useful developer diagnostics do not become certification authority merely because they are easy to compute.

The release rule remains: establish exact source authority, initialize through the native owners, certify that initialized product semantically, build/package through release owners, and promote exactly the qualified bytes.

- Every repository/Plan admission carries explicit source authority (repository subject instance plus filtered source snapshot) separately from semantic preview identity. APP-PASS proof compares that one durable admission authority for both direct verification and strict CI/CD; it never infers source success from execution-mode-specific evidence.
- Strict execution inputs reference the admitted `source_authority_id` and prove their frozen `base_workspace` matches the admitted snapshot. Isolation/runtime evidence may strengthen execution proof but never redefine source authority.
