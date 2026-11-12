# Local test performance and storage boundaries

Use this guide before extending the opt-in local test profile. HUG-210's
2026-11-12 audit classifies all 21 source-recognized families, retains measured
costs and negative results, and identifies the remaining qualification work.
Classification is complete; browser, container, strict, scale and WSL performance
qualification is explicitly incomplete. This guide adds no execution policy or
merge prerequisite. [Make's command map](MAKE_TARGET_SURFACE.yml),
[the CI/CD contract](../CI_CD_CONTRACT.md), native tool configuration and the
existing execution owners remain authoritative.

The shipped profile has a measured, modest architecture benefit. **No additional
RAM allocation, storage relocation, cache or fixture-sharing change is justified
by this audit.** The initial 20% feedback-time goal remains unmet. A negative
result is retained evidence, not a reason to repeat the same experiment unchanged.

## Measurement context and limits

Fresh measurements used main `bb8030f8c86f38205a5b82780d9f95f94d1c2fa8`, tree
`d1f7022382f092cfa718c08177a8c88e254a4218`, canonical source
`sha256:03638f04315d6cfae011cb5fe126055eda4d84d707348c1086a53e4bf2a0c1f8`.
Source stayed unchanged during all measurements. These observations precede
this documentation change and do not qualify its new source identity.

The host was native x86-64 Linux 7.0.0-43, with about 62.5 GiB physical and
55.2 GiB available RAM at admission. The existing resource owner established a
PID-bound cgroup `cpu.max=200000 100000` (two CPUs), with 16 CPUs in affinity.
Jobs ran sequentially; pytest's requested worker ceiling and actual enforcement
are separate observations. The memory estimate is an initial capacity guard,
not a hard memory limit. No RAM reservation was added.

The checkout was ext4. Both `/tmp` and `/dev/shm` were already tmpfs. A default
temporary-directory run is therefore **not a disk baseline** on this host.
No global `TMPDIR`, HOME, XDG or database location was redirected. Native uv/npm
caches and default test isolation remained intact. WSL and `/mnt/c` were absent;
native Linux measurements do not qualify them.

Native tools were Python 3.14.8, uv 0.13.3, Node 22.22.1, npm 10.9.2, backend
pytest 9.0.2, Vitest/V8 coverage 4.1.10, Vite 8.1.5, jsdom 29.1.1 and
Playwright 1.62.1. Standalone projects resolved their own locked environments;
orders used pytest 9.1.1. The loaded Hashmarks producer identity was
`sha256:4c2e57ed864e038c85134ad74b735ec28e35de2f9f0d0111c65471faa80d32c7`.
These are experiment facts, not a second dependency compatibility registry.

Native reports retain exact members, outcomes, collection time, resolved worker
budget, source/tool identity, CPU and cgroup observations where supported.
GNU time retains whole-command CPU, waited-process peak RSS, faults, swaps and
filesystem accounting. Peak RSS is **not aggregate concurrent memory**; cgroup
peaks have their reported lifetime scope. Filesystem accounting does not measure
physical device I/O or prove a cold cache. Page-cache coldness, per-phase disk
latency, scratch high-water bytes, continuous swap/pressure, thermal peaks and
current interactive responsiveness were not measured. They remain unavailable,
not zero. The earlier user's “responsive but high temp” observation belongs to
HUG-209, not these new trials.

Raw source inventories, exact commands, native JSON/TAP/JUnit/coverage reports,
GNU time reports, pair manifests and independent readback belong in HUG-210's
Resources. Reports and archives stay outside the source checkout; see
[HUG-210](https://linear.app/hugloss/issue/HUG-210). The three inventory files are
source observations; their initial execution-status fields are superseded by
the final measurement receipt, not rewritten as if the source scout ran tests.
The attachment `hug-210-native-family-evidence-20261112.zip` has SHA-256
`bdeaf621b83c8aa42e6dc8b9f7fbfd669ec669301a6e577a6b2ffe7fc992fe6a`;
its manifest binds all 272 retained files by path, size and content digest.

## All 21 families

These categories describe source-recognized entrypoints, **not 21 suites that
all passed**. `REPEATED_SETUP_CANDIDATE` names work to profile, not an admitted
cache. `NATIVE_CACHE_ONLY` preserves native tool authority without claiming a
new gain. `DISK_REQUIRED` preserves the tested durable/native storage boundary;
it does not assert that every existing temporary fixture was on disk.
`UNVERIFIED` carries a concrete missing measurement or prerequisite below.
No new `RAM_INPUT_CANDIDATE` was proved.

| ID / owner | Native entrypoint and original execution settings | Classification | Evidence and recommendation |
| --- | --- | --- | --- |
| F01 Backend fast pytest | `make test-python`: unit file batches 4, requested workers 4, 120s; dependency-complete file batches 2, worker 1, 60s. Fast marker excludes slow/process/scale/certification. `test-execution-fast` uses test batches 64, worker 1, 180s. | `REPEATED_SETUP_CANDIDATE` | Frozen six-case recipient-binding pairs show no material profile gain. Selected-Make evidence includes 42 backend unit cases. Profile imports/collection or immutable fixture construction before choosing bytes; retain unique mutable state. |
| F02 Complete/dependency-complete pytest | `test-python-complete`: non-process file batches 4/workers 2/180s, process 1/1/180s; legacy complete alias allows one environment retry. Dependency-complete uses worker 1, 60s non-process and 180s process. `check-backend-tests` uses the selected exact members and configured worker limit. | `UNVERIFIED` | Fresh dependency-capabilities subset: 34 cases passed. Full complete/process portfolio was not measured. No family-wide RAM or completion claim. |
| F03 Tooling / Repository Understanding | `test-tooling`: file batches 4/workers 2/90s, excludes certification/process/slow. Complete tooling: test batches 64/workers 2/180s. Repository Understanding batch owner uses worker 1, batch 4, 90s. | `REPEATED_SETUP_CANDIDATE` | Fresh three-pair 110-case compatibility trial below; frozen selected-Make evidence includes 1,111 tooling cases. Study repeated immutable fixture materialization within one resolved lifetime; do not reuse mutated repositories, secret grader truth or PASS. |
| F04 Backend / cross-owner integration | `test-backend-integration`, `test-cross-owner-integration`: test batches 8, default worker 1, 180s, default marker `not certification and not slow`. Named 100-Run daily retention case has its existing 900s override. | `DISK_REQUIRED` | Fresh two-case backend API/SQLite lifecycle proof only; this bounded scope also excluded daily. Cross-owner/full integration not measured. Keep unique DB/application state, real ports, restarts, WAL/fsync and storage effects. |
| F05 Standalone Iris / orders | `make test-python-projects`: native uv fresh private environments, frozen base+dev group, offline/frozen/no-sync pytest with `-p no:cacheprovider`; original project marker selection. | `NATIVE_CACHE_ONLY` | Fresh original target: Iris 6 passed, orders 19 passed and 1 Airflow adapter deselected. Installation took 379ms/6ms; Iris tests dominated. Keep fresh environments and native caches; no environment/model/PASS reuse. |
| F06 Templates / package native smoke | `verify-p0`: worker 1, test batches 64/180s plus generated freshness; `verify-p1`; `template-startability-certification`: worker 1, batch 8/180s. `backend-package-build-check` creates a fresh editable install. | `NATIVE_CACHE_ONLY` | Fresh editable-package target passed in 0.57s. This does not qualify the full template portfolio, installed wheel, APP or production tools. Preserve genuine bootstrap and native locks. |
| F07 Vitest node | `npm --prefix frontend run test -- --project=node`; `make test-frontend`. Native threads, sequential files (`fileParallelism: false`), isolation, clear/restore mocks, 15s test timeout. | `NATIVE_CACHE_ONLY` | Frozen 26-case pairs show no material gain. No eligible bytes selected; preserve native source/config/transform identity. |
| F08 Vitest jsdom | `npm --prefix frontend run test -- --project=dom`; same original pool/file isolation as F07, original DOM setup/cleanup. | `NATIVE_CACHE_ONLY` | Frozen 13-case pairs show no material gain. Never share a live DOM, timers, globals, mocks, session or app singleton across independent cases. |
| F09 Node browser helpers | `npm --prefix frontend run browser:transport-test` and `browser:fixture-supervisor-test`; native `node --test`, one explicit file per script, no added concurrency. The TCP-probe test is a separate file. | `NOT_APPLICABLE` | Fresh transport 4 and supervisor 5 cases passed; each whole command was under 0.5s. Transport includes a real TCP exchange; supervisor cases use injected children/probes and prove readiness/failure classification, not real Vite spawn/teardown. No material file payload to relocate; TCP-probe corpus was not included. |
| F10 Vitest V8 coverage | `npm --prefix frontend run test:coverage`; broader `frontend-check` includes native check. Same threads/sequential files and original V8 provider/reporting defaults. | `NATIVE_CACHE_ONLY` | Fresh five-file node+DOM subset: 39 passed with fresh V8 summary. Not complete frontend floors. Instrumentation, transform caches and final fresh reports stay native-owned. |
| F11 Python / SQLite / Journal coverage | `coverage-python`, `test-sqlite-coverage`, `test-journal-coverage`: native branch/subprocess coverage, worker 1, file batches (4 in complete gates), 180s. Coverage disables retries/refinement and combines only this invocation. | `DISK_REQUIRED` | Fresh database-path owner: 1 selected file, 40 native cases passed, fresh branch report. No complete SQLite/Journal/package floor claim. Preserve tested DB behavior and same-filesystem atomic publication. |
| F12 Playwright functional / focused | `npm --prefix frontend run test:browser` / `browser:check`; `make browser-targeted-certification` uses seven specs, Chromium worker 1. Functional matrix uses workers 2, `fullyParallel: false`, retries 0; Firefox/WebKit/mobile/tablet keep their original matrix. | `UNVERIFIED` | Matching native browser engines absent in the inspected native cache paths. No browser launch/collection/timing. Existing Vite cache remains native-owned; no unowned fixture server or live context reuse. |
| F13 Playwright visual | `npm --prefix frontend run test:visual`; `browser:release` includes visual comparison. Chromium, workers 2, retries 0, original four-route screenshot configuration. | `UNVERIFIED` | Matching engines and reviewed tracked screenshot baselines absent at audited source. No screenshot run or automatic baseline creation. Reviewed inputs and actual/diff/trace reports retain their owners. |
| F14 Installed runtime browser | `npm --prefix frontend run browser:runtime` through product/release owner; three engines, worker 1, retries 0. Real server, access authority, repository and Run inputs required. | `UNVERIFIED` | No qualified installed runtime/browser identity, production journey or restart measurement. Fixture-backed F12 cannot substitute. Real installed bytes, SQLite and retained Game Tape stay at their storage owners. |
| F15 Locked browser container | `make acceptance-browser-container`: lock-selected Playwright image, native Docker build, `docker run --rm --init --shm-size=1g`, then `make acceptance-release`. | `UNVERIFIED` | Docker daemon available, but image/build/browser/CPU accounting unqualified; current Dockerfile's `make setup` fails native Make dispatch. Host CLI quotas do not establish daemon/container quotas. Keep existing shared-memory size until measured. |
| F16 Product / wheel / container acceptance | `acceptance-product`, `release-accept` / `acceptance-release`, `acceptance-container`; original Ubuntu/openSUSE images, fresh wheel/venv, real restart/readback, unique Compose project/bind state. | `UNVERIFIED` | No built release, container/replacement or acceptance measurement. Preserve install, persistence and Game Tape proof. Compose cleanup has a source-inspected unresolved failure path; see below. |
| F17 APP / native / strict lifecycle | `make certify`, `qualify-development-wsl` and explicitly selected native/provider/model qualification commands; preserve the selected workload's admitted placement and lifecycle. | `UNVERIFIED` | No APP, WSL, custody, signer, installed-model, fs-verity, VM or Kubernetes qualification in this audit. Presence of an executable or device cannot qualify it; kubectl/kind/minikube absent. |
| F18 Scale / stress / retention | `acceptance-scale`, Scout load/HTTP/proxy/overhead/crash/corruption/retention probes and existing real-storage tests; keep original dataset, warm-up, concurrency and measurement windows. | `UNVERIFIED` | No scale campaign or daemon-bound CPU observation; nginx absent for proxy proof. Do not reduce measured storage/process work or share live history to improve a benchmark. HUG-90 owns pressure qualification. |
| F19 Static / types / architecture / generation / build | `make check` / `test`, `architecture`, backend/tooling quality, frontend typecheck/architecture/build, `generate` / `check-generated`, release build. Native Ruff/mypy/tsc/ESLint/Vite/uv/npm semantics. | `NATIVE_CACHE_ONLY` | Retain HUG-209's measured one-use architecture packet handoff. It adds no decoded table or RAM scratch. Other static/build/generator tools have no new paired speedup proof. |
| F20 Hosted PR / daily / release CI | Existing `.github/workflows/ci.yml` and `release-validation.yml`; daily/release selected complete owners, no resumed PASS, separate concurrency domains and native retention. | `NOT_APPLICABLE` | Local RAM rollout does not change hosted selection, scheduling, resource settings or required checks. No hosted performance experiment. Ordinary PR validation for this document is separate from a benchmark. |
| F21 Reports / logs / receipts / promoted artifacts | Existing run logger, pytest report/receipt writers, coverage/JUnit/Vitest/Playwright owners, generated/release and lease-aware storage maintenance. | `DISK_REQUIRED` | No sole authoritative copy on tmpfs. Keep private/no-follow permissions, source/result identity, fsync/atomic publication and active leases. No serialization/retention speedup was measured. |

Entrypoints and settings were traced through the [Makefile](../../Makefile),
[frontend scripts](../../frontend/package.json),
[Vitest config](../../frontend/vitest.config.ts), Playwright
[functional](../../frontend/playwright.config.ts),
[visual](../../frontend/playwright.visual.config.ts) and
[runtime](../../frontend/playwright.runtime.config.ts) configs, and the existing
[backend](../../backend/tests/README.md), [tooling](../../scripts/README.md)
and [browser](../../frontend/e2e/README.md) owner maps. Configured requests are
not observed concurrency or native collected test counts.

## Fresh bounded observations

All successful pytest reports have exact admitted/native accounting, no reused
members, no retries, and no failed/skipped/pending member upgraded to PASS.
Scopes below intentionally bound the experiment; they do not claim that their
entire families ran. Times are whole commands, including original setup/reporting
within the command; CPU is user+system. RSS is GNU time's waited-process maximum.
F03/frontend whole timers include resource-wrapper admission; F02/F04/F05/F06/F11
timers start inside the already-admitted quota. Do not compare those boundaries
as identical setup scopes or sum collection/finalization time onto runner totals.

| Scope at `bb8030f8` | Native outcome | Wall seconds | CPU seconds | Peak RSS KiB |
| --- | --- | ---: | ---: | ---: |
| F02 `test_agent_codex_service_capabilities.py` | 34 passed | 3.36 | 2.14 | 64,816 |
| F04 `test_sqlite_real_usage_lifecycle.py` | 2 passed | 8.38 | 8.29 | 253,312 |
| F05 original `make test-python-projects` | Iris 6 / orders 19 passed; orders 1 deselected | 19.03 | 22.63 | 141,024 |
| F06 original `make backend-package-build-check` | Native install + source-layout check passed | 0.57 | 0.50 | 37,092 |
| F09 original `browser:transport-test` | 4 passed | 0.434 | 0.38 | 76,696 |
| F09 original `browser:fixture-supervisor-test` | 5 passed | 0.388 | 0.41 | 78,376 |
| F10 five authored files, V8 | 39 passed | 7.347 | 10.40 | 586,840 |
| F11 `test_database_path_ownership.py`, branch coverage | 1 file member / 40 native cases passed | 7.50 | 3.08 | 66,960 |

F04's native batch observed about 281 MiB cgroup peak; F11 about 111 MiB.
Those are distinct from RSS and not a general family memory allowance. F05
verified both process quiescence and temporary-environment cleanup. Native uv's
cross-filesystem hardlink-to-copy warning remained visible; forcing link mode
would change the experiment. Its measured install cost is too small to justify
replacing fresh environments. Iris's actual training/CLI tests took about 16.2s;
orders about 0.5s. Do not cache those tested results.

F03 used the existing qualified runner on
`scripts/tests/test_architecture_source_snapshot.py` and
`scripts/tests/test_runtime_authority_registry.py`: test granularity, requested
workers 2, batch size 64, timeout 180s, marker `not certification`, no resume.
Each invocation passed the same **110 native cases**, with identical source and
toolchain identity. Mode order alternated ordinary/RAM, RAM/ordinary,
ordinary/RAM. Native collection was 565–616ms.

| Pair | Ordinary whole seconds | Profile whole seconds |
| --- | ---: | ---: |
| 1 | 15.603071 | 14.638816 |
| 2 | 14.860879 | 14.599106 |
| 3 | 14.946918 | 14.569836 |

This is a compatibility/cost observation, **not a new causal RAM speedup**.
These tests explicitly exercise both source transports themselves. The first
baseline's startup/page-cache state was not controlled; a mode label alone
cannot assign the variation to the profile. No cache/fixture change was adopted.
Whole CPU means were 16.403333/15.790000s and waited-process peak RSS means
526,513/522,555 KiB, ordinary/profile respectively. These describe the same
mode-label observation and do not strengthen its causal interpretation.

Exact F10 files were `MutationController.test.ts`, `readResources.test.ts`,
`runStepUrl.test.ts`, `CatalogPage.test.tsx` and `GameTapeOutputs.test.tsx` at their
existing authored paths. Native coverage outputs were fresh, outside source;
the original pool, sequential files and V8 flags were preserved. F11 used
`oh_goon.runtime.database`, one file batch, worker 1 and the original coverage
owner; its `--coverage-fail-under 0` is a diagnostic subset measurement, not a
complete coverage-floor admission. The archive contains full arguments.

## Frozen paired evidence and rejected approaches

HUG-209's [whole-workflow measurements](../DEVELOPMENT.md#whole-workflow-compatibility-measurements)
were frozen at `d401cf991c54dbe810612b6f959c4c376cec0675`, source
`sha256:080f25364e11f2169ad2d2e3f6b295deb0d70b409d45560c80132bfce3aecc20`.
Only `docs/DEVELOPMENT.md` changed between that commit and audited `bb8030f8`;
runtime code, native locks and commands were equal. This establishes applicability
for planning, **not transfer of exact-source qualification**. The expensive six
Make trials were not repeated for a documentation audit.

Each row below had three alternating paired trials under two CPUs, no reused
pytest PASS and exact native outcome comparison. Each short-lane timing covers
two fresh invocations. The unit subset explicitly selected `-k 'not unmigrated'`,
leaving one legacy prose assertion out; it is not the full file/owner.

| Frozen scope | Ordinary mean seconds | Profile mean seconds | Disposition |
| --- | ---: | ---: | --- |
| F01 six recipient-binding cases per invocation | 5.613192 | 5.595824 | No material gain |
| F07 26 node cases in three files per invocation | 3.871403 | 3.889354 | No material gain |
| F08 13 DOM cases in two files per invocation | 8.753081 | 8.794741 | No material gain |
| Selected `make test`: static + 1,153 cases each invocation | 485.056170 | 481.500367 | 0.73% observed lower wall; 20% goal unmet |
| F19 complete 24-check architecture command | 78.207717 | 74.706892 | 4.48% observed lower wall; retain one-use handoff |

The selected-Make membership was **42 backend unit + 1,111 tooling cases**;
no dependency-complete, backend integration, cross-owner integration or standalone
project cases were in that selection. Vitest ran in the separate paired lanes.
The aggregate is a real selected `make test` invocation with its static phases,
not the complete frontend/backend/integration portfolio. A fresh-main or
documentation-only `make test` selects different work and is not a comparable
backbone timing. Its frozen CPU mean was 818.51→814.01s; the architecture mean
was 78.16→74.67s with roughly unchanged 1.60 GiB waited-process peak RSS.

A single combined workflow containing Vitest, backend/tooling and static/build
has **not been comparatively measured** by these trials. Separate paired lanes
do not fill that requirement. HUG-211 must retain this unverified feedback-time
question before claiming a combined workflow benefit; do not extrapolate or
repeat the expensive selected-Make experiment as if it supplied missing Vitest
membership. Frozen short-lane ordinary/profile CPU means were 5.027/5.037s
(unit), 4.950/4.923s (node) and 12.757/12.823s (DOM); their waited-process RSS
means were 87,320/87,229, 436,123/444,041 and 578,259/578,759 KiB respectively.

Retain the [full negative experiment record](../DEVELOPMENT.md#cache-experiment-evidence):

- The Node/tmpfs cache pilot increased whole short-scope cost; typecheck+ESLint
  was 22.64→23.38s in its one-pair pilot. Cache admission/population/cleanup cost
  belongs in the measurement. `/tmp` was already tmpfs; policy and placement
  changed together, so these are not pure RAM-versus-disk trials.
- Native Vitest 4.1.10 experimental `fsModuleCache` returned a stale JSX transform
  after changing only `tsconfig`'s JSX factory. The changed input passed with
  the cache disabled. Do not enable this cache or invent a consumer invalidation
  algorithm; reconsider only after a native/upstream fix and mutation/control
  proof against the actually installed version.
- A 64 MiB decoded-packet retention experiment made complete architecture
  slightly slower (78.72→79.19s) with higher memory. It was removed. An isolated
  926-request phase improvement did not establish end-to-end benefit.

## Eligible bytes, lifetime and cleanup

No new byte class was admitted for RAM storage. Exact sizes and savings for
unproven candidates remain unavailable. A candidate never grants permission to
reuse live state or native results.

| Families | Possible immutable input and original lifetime | Boundary that must remain |
| --- | --- | --- |
| F01–F03 | At most profiled source/fixture payloads within their existing private fixture or source-capture scope; no chosen new bytes or extra memory budget. | Native Hashmarks source/producer/policy freshness and mutation negatives; original test collection; no shared mutable repo or SECRET grader truth. Scope owner closes on success/failure. |
| F04 / F11 | Independent immutable request/contract bytes only if measured; no selected relocation. | Unique DB/app handles, SQLite/Journal/WAL/reopen/fsync and process behavior. Test fixtures close clients, DB and overrides; coverage session combines fresh attempt files, then publishes via destination-sibling temporary file. |
| F05–F06 | Native uv download artifacts and read-only packaged resources, under existing native rules. | Fresh owned environments, install/entrypoint/startability proof and actual model/transform work. Process quiescence precedes deletion; uncertain cleanup retains scratch and fails its own report. |
| F07–F10 | Native tool-owned transform/coverage intermediates only; none newly selected. | Native semantic cache keys; separate process/file/DOM/mock lifetimes. F09 must bind/exchange/close sockets. V8 must emit fresh coverage; retained JSON/summary stays outside volatile-only storage. |
| F12–F13 | Native Vite/browser rendering intermediates only after whole-invocation benefit and invalidation proof. | Original fixture process-group readiness/TERM/KILL, fresh browser contexts, reviewed snapshots and retained traces/diffs. No implicit unowned server reuse. |
| F14–F18 | At most separately measured read-only setup outside the actual persistence, installation, custody or resource proof. | Real runtime/repository/DB/history, installed bytes, daemon/container/kernel authority, original load and restart assertions. Do not relocate the condition being measured. |
| F19 | Existing exact native parsed source packet already allocated by Hashmarks; immediate one-use delivery. | Canonical evidence/source/producer/policy guards remain. No decoded table, new parser/cache key, result cache or extra reservation. Source scope closes deterministically. Other tools keep native cache lifetimes. |
| F20 | None through a local RAM rollout. | GitHub owns scheduling/checks; original 7/30-day evidence retention and native caches remain. |
| F21 | Only bounded transient serialization/stream buffers while the existing writer owns publication. | Durable authoritative reports, receipts, coverage, screenshots, Journal/Game Tape and release artifacts. Same-filesystem atomic replace/fsync, private permissions and active leases; no generic age-based cleanup. |

## Ranked next work

1. **Keep the shipped architecture handoff.** It removes one duplicate decode
   within the native observation lifetime and has complete-command paired
   benefit. Memory cost is the packet already in use, with no retained table.
   Any extension must prove the actual architecture command again, including
   source/producer/policy mutation and failure cleanup.
2. **Profile repeated setup before extending it.** F01/F03 are candidates because
   collection/imports and repository fixture construction repeat. Their native
   collection observations and exact case receipts provide a baseline; they do
   not show which construction is redundant. Isolate a named owner, measure
   saved construction plus key/admission/cleanup cost, and preserve per-test
   mutable state. Expected bytes and end-to-end savings are unproven.
3. **Retain native uv/npm/compiler caches.** F05's installs were milliseconds;
   caching an installed environment or trained model would remove tested work.
   F07/F08 have no demonstrated profile benefit, and the examined native cache
   has a stale-input defect. Do not repeat those approaches unchanged.
4. **Repair and qualify acceptance prerequisites before browser/container
   optimization.** Missing matching browsers and reviewed visual baselines leave
   F12–F14 unverified. At F15, [the Dockerfile](../../docker/playwright/Dockerfile)
   calls `make setup`; native dispatch returned 2 because no such target exists.
   Do not add a legacy alias: use the actual bootstrap owner and prove the locked
   image entry boundary. Host cgroups cannot establish Docker/BuildKit/Compose
   CPU quotas; actual daemon-created workloads need their own native witness.
5. **Close cleanup observation at the existing Compose owner.** Source inspection
   of [container acceptance](../../backend/src/oh_goon/repository_tooling/release/accept_container.py)
   found that `_cleanup_compose` discards cleanup status/output before temporary
   state can be deleted. A live leak was not reproduced. Add a focused valid
   baseline plus cleanup-failure/cancellation proof at that owner before any
   scratch-placement claim; retain active state and report teardown failure
   truthfully. Do not add another lifecycle or scheduler.

[HUG-212](https://linear.app/hugloss/issue/HUG-212) owns the concrete acceptance
bootstrap repair and cleanup investigation in items 4–5, separately from a
performance adoption claim.

HUG-211 remains a conditional owner-specific experiment, not blanket RAM rollout.
[HUG-12](https://linear.app/hugloss/issue/HUG-12) retains CPU/WSL qualification,
[HUG-90](https://linear.app/hugloss/issue/HUG-90) pressure/OOM/I/O qualification,
and [HUG-115](https://linear.app/hugloss/issue/HUG-115) pytest semantic reuse
qualification. No negative result closes those separate requirements.

Before adopting another optimization, run at least three matched pairs for the
named beneficiary, freeze native source/membership/tool/CPU/worker context,
separate cold/warm and storage effects where actually observable, include all
setup/cleanup cost, and prove stale/corrupt input, mutation, cancellation and
cleanup behavior through the existing owner. Missing metrics or native
prerequisites remain unverified. Full daily, release, APP and strict portfolios
stay separately selected; this audit does not make them ordinary PR prerequisites.
