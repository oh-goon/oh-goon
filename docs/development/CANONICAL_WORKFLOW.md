# Canonical developer workflow

The ordinary developer path has one initialization frontdoor and three deliberately different verification levels:

```bash
make init
make check
make verify
make certify
```

`make check` is fast inner development feedback. It runs bounded fast backend/tooling plus architecture/generated-contract and frontend type/unit checks, continues independent areas, and reports PASS/FAIL/BLOCKED. It is not certification authority.

`make verify` is exhaustive diagnosis. It projects every Goon from the canonical APP certification Plan onto its complete repository-owned Make target, continues independent failures, and reports the whole application state. It is deliberately not a second release orchestrator and cannot create APP PASS.

`make certify` runs the canonical APP verification Plan directly against the initialized checkout and remains the final fail-fast APP certification authority. Before it acquires certification state or creates a Fleet Run, the outer frontdoor must prove canonical source-layout validity and canonical Goon/static execution authority. A failed prerequisite ends there: no state lease, `RUN_ADMITTED`, repository attempt, immutable-input preparation, or Goon execution may exist merely so a later Goon can rediscover that the run was invalid. It must not discover, inherit, validate, materialize, or export strict release/CI executor authority that the verification Plan does not consume. The outer certification frontdoor classifies APP verification and strips strict executor/cgroup/systemd-delegation authority before static admission or any APP bootstrap/orchestration work; the workflow bootstrap strips the strict transport namespaces again as defense-in-depth; the APP local-runtime Goon uses `make local-runtime-verification` and cannot reacquire strict placement authority downstream. `make strict-executor-setup` and `make strict-local-runtime-acceptance` are explicit host/strict-acceptance capabilities, never APP prerequisites.

## Admission before work

The execution invariant is purpose-independent:

```text
resolve required authority
    -> validate every effect-free prerequisite that can invalidate the run
    -> freeze/transport admitted facts
    -> RUN_ADMITTED
    -> claim attempt
    -> prepare execution inputs
    -> execute once
```

One semantic question has one owner: **is this repository plus resolved Plan admissible to execute?** Every Fleet Plan purpose uses the same base `FleetAdmissionPlanner` decision. Strict CI/CD may add stronger topology, repository-requirement, executor, isolation, resource, and placement proofs, but it never replaces or forks that base decision. Verification is not a relaxed admission mode.

Runtime preparation may revalidate an already-admitted fact against drift immediately before effect-start, but it must not own the first validation of a prerequisite that was already knowable before admission.

Admission does **not** pre-run the requested workload. A test failure, verification finding, command exit code, or other result produced by the admitted operation remains execution evidence. The pre-work boundary owns only facts that decide whether execution is allowed and sufficiently specified to begin; it must not turn expected workload outcomes into shadow admission policy.

`make init` is the sole dependency-materialization frontdoor. `make verify` owns broad diagnostics; `make certify` owns the lean release-critical Plan. There is no separate certification dependency setup path.

Release preparation remains one separate canonical release Plan via `make prepare-release`.
