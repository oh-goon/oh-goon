# B12E-C1 — ONE shared Linux physical-observation implementation (B21)

**Classification:** development implementation / read-only. **Gate A/B/C, APP PASS and release: BLOCKED.**

## One low-level owner, no new runtime execution chain

Existing original Fleet admission → original `RESOURCE_INTENT` → Bubblewrap allocation → original LACE cleanup/recovery remains intact. B21 does **not** connect a new physical fact publisher, reissue Run authority, or classify final outcome.

```text
core/ports/physical_observation.py            immutable PhysicalObservationScope,
    │                                          PhysicalKernelFact, PhysicalKernelSnapshot
    ▼
integrations/linux_physical_observer.py         ONE implementation of kernel /proc,
    │                                          cgroup, mount, namespace, fdinfo,
    │                                          network inventories and double scan
    ▼
repository_tooling/local_lace_physical_preflight.py
                                               operator scope parsing only,
                                               thin read-only diagnostic projection,
                                               DEVELOPMENT_DIAGNOSTIC_ONLY
```

- `PhysicalObservationScope` is a **typed observation target**, not a signed original Run or LACE authorization. A caller-selected Run ID or resource path has no journal authority.
- `PhysicalKernelSnapshot` holds immutable kernel components and does not expose `ABSENT`, `quiescence_proven`, `teardown_proven`, `gate_b`, effect permission, terminal facts or an authenticated-Run claim. `NOT_OBSERVED` is not physical absence. The consumer gets a copy via `to_mapping()`.
- `linux_physical_observer.observe_kernel()` is the sole two-scan mechanism. A diagnostic-only injection seam preserves adversarial B13–B19 tests; **production may not use it to trust operator inputs**.
- The original CLI continues to parse the exact operator JSON scope, projects the shared facts, and asserts `effect_permission=NONE`, unverified physical obligations and `gate_b=BLOCKED`. Private test aliases in the CLI contain **no kernel implementation**.
- B21 adds X0 regression checks that reject a second diagnostic kernel parser, a missing shared observer, authority imports into the mechanism, and a CLI disconnected from the shared observation call. These checks are **not a complete runtime call graph proof**.

## Preserved Linux mechanisms and hazards

Cgroup root identity, subtree population and member birth, process boot/PID start time, observer and tracked mount inventories, original namespace handles, foreign namespace holder census, foreign mount target census, original mount-ID-bound fdinfo, named netns/veth/nft discovery, bounded budgets, substitution/identity races and two independent reads. Unknown facts stay UNKNOWN, and scoped negative observations never become a quiescence claim.

## Remaining mandatory work

**B12E-C2** must bind the shared facts through the **existing authenticated original-LACE cleanup/recovery owner** to the signed original Run, Step, signer epoch, executor generation, `RESOURCE_INTENT`, original resource handles and the original causal journal. Exactly one LACE physical-fact publisher and one Enforcer outcome classifier. **No CLI→production edge.** Requires separately provisioned issuer/root evidence.

**B12E-C3–C6** must prove real Bubblewrap post-cleanup absence using independent complete native Linux observations and bounded convergence; unknown or missing coverage blocks physical closure. Demonstrate no alternative cleanup or terminal authority, and separate OpenSandbox provider proof. Host-side unit tests or a single WSL development witness cannot satisfy these gates.

**Native B21**: exact-source `make qualify-development-wsl`; inspect separately supervised PHYSICAL_OBSERVER ring including `test_b21_shared_observer_collapse` with clean OS process receipts. 10-repeat stress is a distinct run, not implicit certification.
