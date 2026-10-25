# Development

For the short operator/developer navigation path, start at [`docs/README.md`](README.md). This document owns development detail; it is not a second command catalog.

Critical regression strategy: [`development/REAL_LIFECYCLE_QA.md`](development/REAL_LIFECYCLE_QA.md). Critical behavior is not considered proven by component tests alone; production-shaped lifecycle and adversarial proofs are the release-confidence layer.

Test design/cleanup policy: [`development/TEST_QUALITY.md`](development/TEST_QUALITY.md). Use it to choose the proof layer, avoid false-confidence tests, and review mocks/fixtures/timing/skips without creating a test-quality meta-suite.

```bash
make init        # complete first-checkout bootstrap
# Re-run make init whenever the committed locks need to be materialized.
make dev
```

Ordinary Linux/WSL development uses the standard execution lifecycle. Additional
browser or host preparation is needed only for the checks or workloads that
select those capabilities. For read-only environment diagnosis, use
`make diagnostic-system-check` after initialization. For browser work:

```bash
make diagnostic-system-check
make browser-setup
# If preflight reports missing host libraries, install them explicitly (may require sudo/root):
make browser-system-deps
make browser-preflight
```

Repository-wide native-tool policy is owned by [Native tool authority](development/NATIVE_TOOL_AUTHORITY.md): Oh-Goon orchestrates native tools and never reimplements their semantics. The Python rules below are one application of that general boundary.

Oh Goon does not probe or select host `python*` command names. `backend/pyproject.toml` owns Python `>=3.12,<3.15` and dependency intent; committed `backend/uv.lock` is native uv's exact resolution authority; native uv `>=0.10.0` owns interpreter discovery and `backend/.venv` materialization. The lock is part of source/package/certification identity and public-source publication checks reject private, host-local, and repository-local dependency identity from it. Full-project materialization uses `--locked` so stale lock authority is rejected; repeated no-sync execution may use `--frozen` because it consumes an already-qualified lock without authoring or materializing. `make sync` is the intentional authoring boundary allowed to update the lock.

Standalone Python examples own their native project and lock at
`examples/<project>/{pyproject.toml,uv.lock}`. Each lock and its sibling project
file must be regular, non-symlink source files. These locks and the existing
certification-fixture locks describe their isolated workloads; they do not
replace backend dependency authority. Other root or nested `uv.lock` paths
remain invalid. Native uv owns project validity and lock freshness.

`make init` is the sole idempotent developer materialization frontdoor. It invokes native uv and npm directly; those tools own resolution consumption, caches, retries, environment creation, and installation semantics. There is no separate developer acquisition/setup command. The dependency-resolution adapter remains only for repository-specific public-source policy; native uv itself owns lock freshness through `uv lock --check`/`uv sync --locked` and intentional `make sync` authoring. `make init` is a separate supply-chain proof over materialized native-tool outputs. Use `make sync` only after intentional dependency-intent changes; native uv/npm author the committed lock bytes.

Hashmarks is a mandatory backend dependency supplied through that same
`pyproject.toml` / `uv.lock` contract. The single
[`integrations/hashmarks/`](../backend/src/oh_goon/integrations/hashmarks/)
adapter owns consumption of its repository-evidence APIs; installing or changing
the dependency does not introduce a second repository-identity implementation.
See [Agent Skills](AGENT_SKILLS.md) for the bounded Agent execution surface and
native local-model qualification.

Before `make sync` publishes new Python lock bytes, its native uv preflight builds the backend in the same **editable** mode used by project materialization, inside an ephemeral venv and without installing project dependencies. This is deliberately not `uv build --wheel`: standard-wheel construction belongs to the release lifecycle and must continue to fail closed until the release owner has produced the generated frontend payload.

The frontend runtime requirement is owned by `frontend/package.json` `engines` (Node `>=22.16.0`, npm `>=10.9.2`). `packageManager: npm@10.9.2` is the lock-authoring/contributor baseline rather than a claim that newer compatible npm releases are unsupported. Release/browser containers intentionally pin their Node/npm images for reproducible certification. Do not add a second Node-version file unless real tooling requires one.

The frontend intentionally keeps npm peer validation enabled. `eslint-plugin-jsx-a11y` 6.10.2 declares an ESLint peer range only through v9, so `eslint` and `@eslint/js` are pinned together at `9.39.5`. The repository also keeps the scoped resolution requested for the plugin:

```json
{
  "overrides": {
    "eslint-plugin-jsx-a11y": {
      "eslint": "$eslint"
    }
  }
}
```

The override binds dependency resolution to the compatible root ESLint dependency; under npm 10 it is **not** a substitute for a peer range that excludes the selected major. Do **not** use `--legacy-peer-deps`, `force`, or an `.npmrc` relaxation to run ESLint v10 while the plugin still excludes it. `make frontend-install` is a thin native `npm ci --prefer-offline --ignore-scripts --no-audit --no-fund` frontdoor. Oh-Goon does not maintain a parallel `node_modules` cache identity, symlink farm, graph cache, publisher race protocol, or cache GC for npm. npm itself owns `package.json`/`package-lock.json` parity, cache behavior, installation and graph semantics. `make frontend-lock` is the native npm frontend lock-authoring capability; the normal cross-ecosystem authoring workflow is `make sync`, which invokes Python resolution authoring and frontend lock reconciliation explicitly. When upstream jsx-a11y declares support for a newer ESLint major, update the root ESLint pair first, prove a normal strict install/lock, then remove this compatibility guard if it is no longer useful.

Backend dependency materialization is owned directly by uv. Normal development uses one native
operation: `uv sync --project backend --locked --group dev --group quality --group build`. uv owns
environment creation, editable project installation, package acquisition, caching, retries, registry
interaction, and lock consumption. Oh-Goon does not split that operation into its own dependency-sync
and project-install phases. Frontend materialization follows the same rule with native `npm ci`.

**Native-tool authority rule:** when a native tool already owns a semantic operation, Oh-Goon may
select when it runs, provide already-resolved inputs, isolate/bound the execution, and report its result,
but must not add a second resolver, cache protocol, retry algorithm, lifecycle model, or fallback proof.
A native command blocked by an external dependency remains the native failure/`INCOMPLETE` evidence;
it is not an invitation to synthesize another proof. `make sync` is the intentional dependency-authoring
boundary; native uv/npm author the committed lock bytes. `oh-goon-qualification-exec` binds each actual pytest/ruff/mypy invocation to the prepared project
interpreter at execution time. There is no separate ceremonial qualification preflight. Frontend
qualification likewise runs the real npm package script directly; there is no `npm ls`/`.bin` readiness
mirror. `make init` is the normal developer startup/materialization path.

HTTP ownership is singular: Oh Goon product code uses `httpx2`, including GitLab component fetching, Starlette/FastAPI test transport, and OpenSandbox lifecycle admission. OpenSandbox is an execution provider rather than Python dependency authority; Oh-Goon does not import or depend on the provider SDK. Remote sandbox file/command effects use an explicitly configured `osb` executable whose bytes participate in executor configuration identity. The API key remains environment/header authority and is never placed in CLI argv. The deny-all OpenSandbox backend admits only Kata or Firecracker, and requires endpoint/runtime deployment-attestation evidence bound to an exact server-configuration SHA. Legacy `httpx` is therefore absent from the Oh-Goon universal dependency graph unless another independently owned dependency later requires it.

Useful contributor commands:

```bash
uv run --offline --frozen --no-sync --project backend oh-goon dev explain-run <run-id>
make architecture
make generate
make check-generated
make check
```

The project has one native Python engine. There is no standalone compatibility engine.

`make architecture` runs only the small repository-owned cross-owner boundary set. It is not an installed Oh Goon command and does not replace behavioral runtime tests.

- [Fleet plans, templates, extensions, and the YAML/TOML/Python authority split](development/FLEET_PLANS.md)
- [Shared Plans and Shared Runs](development/SHARED_PLANS.md)
- [Saved local Lineups and Shared Plan Enforcer preview](development/LOCAL_LINEUPS.md)

## Repository understanding certification

Agent-oriented repository comprehension is testable without granting write authority. The version-bound pilot lives under [`docs/certification/`](certification/README.md): candidate-visible questions are separate from grader-only gold answers, and `uv run --offline --frozen --no-sync --project backend oh-goon-repository-understanding-exam prepare <destination>` creates an answer-free checkout for the blind pass. The caller must restrict the candidate to that prepared checkout; prompt instructions alone are not an answer-access boundary.

### Fast certification repair loop

After a backend APP failure, prefer bounded `make test-python` or `make verify-p0`; run full `make certify` only after the focused repair is green. The inner backend gate has 120s batch / 600s suite hard timeouts and excludes explicit slow/process/scale/certification tests. Final APP backend qualification uses `make test-python-complete` with 180s batch / 1200s suite hard timeouts. `make init` is native dependency materialization, not a per-edit command.

## Dependency cache authority

Oh-Goon does not own package-manager or browser caches. Native uv, npm, and Playwright remain the sole cache/materialization authorities. Oh-Goon therefore exposes no cache status/prune/clean compatibility command and does not recognize historical Oh-Goon dependency-cache locations. Current Oh-Goon-owned persistent state is reported and pruned only through the storage lifecycle (`make storage-status` / `make storage-prune`) when that lifecycle has positive ownership/disposal proof. Unknown old cache directories are simply unclassified host bytes and receive no product cleanup authority.

## Session-independent continuation authority

Ordinary Git-backed contributions use the selected branch/commit and reviewed
working-tree changes as source. Preserve authorized work with normal Git
commits and synchronization; a fresh clone does not need checkpoint history,
archive handoffs, or a `RECOVERY_STORE`. The archive workflow below applies
when explicitly continuing an archive-managed campaign or requesting a portable
checkpoint. Its verified recovery chain remains authoritative even if the
extracted worktree also uses Git.

Archive-managed development continuation assumes total loss of chat/session context **and** loss of the current container. The invariant is: **Changing sessions or losing the current container may lose all conversational and workspace state without losing project authority, completed-phase evidence, or the remaining execution plan.** The normative continuation model, machine-readable phase ledger, recovery-HEAD relationship, checkpoint cadence, and generated handoff projection are defined in [`development/DURABLE_CONTINUATION.md`](development/DURABLE_CONTINUATION.md).

The repository recovery owner emits the durable continuation record itself and digest-binds it into the publication receipt and `RECOVERY_HEAD.json`. A Markdown handoff is generated from verified committed state and is never an independent authority. Conversation history, model memory, and ephemeral workspace bytes are never recovery inputs.

## Archive-managed development-candidate recovery checkpoints

A mutable development workspace is disposable. Long repair/dogfood campaigns use the repository-owned recovery graph described in [`development/DURABLE_CONTINUATION.md`](development/DURABLE_CONTINUATION.md); conversation history, model memory, `/mnt/data`, and the current container are never continuation authority.

Deep product dogfood reuses the exact-artifact real-lifecycle owner and must remain public-boundary, opt-in diagnostic evidence rather than certification authority; see [`development/REAL_LIFECYCLE_QA.md`](development/REAL_LIFECYCLE_QA.md#deep-dogfood-boundary).

The durable invariant is: **Changing sessions or losing the current container may lose all conversational and workspace state without losing project authority, completed-phase evidence, or the remaining execution plan.**

Keep the semantic direction coherent but the loss window deliberately small. Create a recovery generation **before the first mutation**, **after every meaningful durable-byte closure plus its focused proof**, before a risky/long mutating operation that could reset the environment, before an intentional handoff with unpersisted mutations, and at the final candidate boundary. Treat each such source/lock/generated/recovery mutation as a **micro-checkpoint boundary**: one coherent mutation, its focused proof, durable persistence, and independent read-back before the next mutation. Successful read-only qualification does not require a ledger-only generation; continue the ordered qualification ladder in the same restored full-checkpoint workspace. If that workspace is lost, restore the latest independently verified full checkpoint and rerun those read-only proofs. Monotonic intermediate names such as `GEN45_M1`, `GEN45_M2`, then final `GEN45` are encouraged when durable bytes change; archive/source/receipt identities remain authority. Recovery generations are durability boundaries and do not require VERSION churn.

For manual/exported handoff, use a **full working checkpoint** whenever the recipient is expected to extract a directory and execute commands directly. A `*_DELTA.zip` is transport-only and is never a checkout: it must use the `payload/` namespace defined by `DURABLE_CONTINUATION.md`, be verified against its parent in external staging, and be applied inward before execution. Never `cd` into an extracted delta and run `make`, uv, npm, or Oh-Goon from it.

`make recovery-checkpoint` requires an explicit `RECOVERY_CONTINUATION_INPUT` stored outside canonical source. That input contains the phase/evidence ledger only; it is transport/authoring intent, not source and not authority. The recovery owner packages the exact source with repository-owned `package_slim`, fills observed source/archive/generation/proof identities, publishes an immutable machine-readable continuation object, digest-binds it into the publication receipt and `RECOVERY_HEAD.json`, verifies durable readback, and only then CAS-advances HEAD. A crash before CAS leaves the old generation authoritative; a crash after CAS leaves the new generation fully reconstructable.

The committed continuation record owns `current_authority`, `completed_phases`, `open_phases`, `proof_receipts`, `qualification_state`, and `next_executable_phase`. `next_executable_phase` must be present in the open ledger. PASS, FAIL, BLOCKED, and INCOMPLETE are preserved exactly; checkpointing never upgrades evidence.

`next_executable_phase` identifies the first currently admissible bounded phase; it is never a shell recipe. A phase that mutates source, locks, generated authority, recovery state, or another durable authority must be persisted and independently read back before another mutation begins. A successful read-only qualification phase may advance locally to the next ordered read-only phase without creating a ledger-only checkpoint; if the workspace is lost, those PASS results are rerun rather than reconstructed from memory.

Use:

```bash
make recovery-checkpoint \
  RECOVERY_STORE=/durable/oh-goon-recovery \
  RECOVERY_CAMPAIGN_ID=<campaign> \
  RECOVERY_EXPECTED_GENERATION=<generation> \
  RECOVERY_EXPECTED_HEAD_SHA256=<exact-head-sha> \
  RECOVERY_CONTINUATION_INPUT=/durable/continuation-input.json

make recovery-status RECOVERY_STORE=/durable/oh-goon-recovery
make recovery-status RECOVERY_STORE=/durable/oh-goon-recovery RECOVERY_FORMAT=markdown > /durable/oh-goon-recovery-handoff.md
make recovery-resume RECOVERY_STORE=/durable/oh-goon-recovery RECOVERY_DESTINATION=/new/empty/workspace
```

Generation zero omits `RECOVERY_EXPECTED_HEAD_SHA256`; later generations require the exact digest restored with the expected generation. `recovery-status` verifies and returns HEAD plus continuation state. Markdown output is generated from that verified state and is a human projection only. `recovery-resume` restores only into an empty destination and verifies the resulting canonical source identity.

Recovery transport must never mutate the source it protects. `RECOVERY_STORE`, `RECOVERY_CONTINUATION_INPUT`, generated Markdown handoffs, and `RECOVERY_DESTINATION` must remain outside the current canonical source root. The repository-owned recovery CLI rejects in-tree filesystem store/input/destination paths. Do not work around that guard with an ignore rule or silent cleanup phase.

If the container disappears after the most recent committed recovery generation, later mutations are `LOST_UNPERSISTED`. Resume from committed HEAD, read `next_executable_phase`, reapply that work, and rerun its proof. Never reconstruct vanished bytes or PASS claims from remembered chat/session outcomes.

### Python/uv dependency authority and constrained internal runners

Dependency diagnosis MUST preserve four different facts instead of collapsing them into “package available/unavailable”:

1. **Dependency intent** is owned by `backend/pyproject.toml`. Minimum-compatible ranges are semantic intent; for example, `cryptography>=46.0.4` must not be rewritten as `cryptography==46.0.7` merely because the current lock selects 46.0.7.
2. **Lock authority** is `backend/uv.lock`. It records the exact selected resolution consumed by frozen workflows. Lock authoring/reconciliation is repository-owned (`make sync` and the lock checks); never hand-patch the lock to satisfy a particular runner.
3. **Developer materialization** is owned by native uv and npm through `make init`. Their caches and registry behavior are native-tool implementation details, not Oh-Goon authority. A cache miss or registry failure is reported as the native operation observed it and is never evidence that a dependency is globally unavailable.
4. **Certification and release verification** consume the same committed native locks and native package-manager semantics as development. Oh-Goon does not create a separate retained package universe, resolver, cache authority, or offline reconstruction protocol for certification.

Constrained hosted/internal runners add a fourth **diagnostic-only observation**: a dependency can already be importable from the runner's host/tool environment while the repository project environment is not materialized. The reverse can also occur. Report these facts independently. Host/internal importability can enable bounded diagnostics, but it never substitutes for the repository's native `uv sync --locked` environment when that environment is required.

Therefore diagnostics must say **“required artifact is absent from the selected uv cache”**, never “uv lacks dependency X” or “dependency X is unavailable” unless the evidence actually establishes that stronger claim. Do not phrase a cache miss as an exact-version dependency absence. Oh-Goon's declared cryptography intent is the range in `backend/pyproject.toml`; an exact version such as 46.0.7 may simply be the current locked selection.

The normal order is: re-establish `pyproject.toml` intent → let native uv/npm author locks only when dependencies intentionally change (`make sync`) → `make init` through native uv/npm → run explicit diagnostics only when they answer a separate question → `make verify` for broad diagnostics → `make certify` for the lean release-critical authority path. A cache or registry failure is reported as the native tool observed it; Oh-Goon does not build a substitute dependency proof around that failure.
