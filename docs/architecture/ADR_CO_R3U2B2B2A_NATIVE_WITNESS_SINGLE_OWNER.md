# ADR — A native positive test cannot outrun current LACE authority

## Decision

The original QENV1 CORE native qualifier is the *only* canonical supervisor.
A historical positive CO-R3N cgroup effect test becomes inapplicable when
later source revisions deliberately refuse native effects. Under an explicit
`QUALIFY_R3N_CGROUP_ROOT`, the current qualifier records a BLOCKED status
and fails before entering a native effectful test. It may never fake a permit
with a fixture, skip the legacy witness and promote an unrelated green run,
or open a second independent certification lane.

The same CORE selects a read-only real-host refusal suite even without a
delegated writable parent. Its green receipt means **the current original
LACE effect owners reject unprovisioned work on the observed host**; it says
nothing about custody when that gate is later enabled, foreign writers,
revocation, complete teardown, or release.

The historical native witness remains in source as an unqualified artifact.
Re-enrollment requires a new architecture decision backed by genuine
independently enforced Linux exclusive-writer custody at the syscall cut.
A static code check cannot substitute for hostile native experiments.

## Authority and tests

- Enforcer owns signed Run/Step, trust epoch, executor generation, admission.
- Original LACE owner owns all kernel effects and physical observations.
- One existing QENV1 CORE supervisor owns negative and later positive tests.
- No Hashmarks dependencies or owner migrations.
- X0 rejects removal of the pre-positive BLOCKED control and single-CORE
  enrollment, while legacy CO-R3N architecture tests remain enforced.

Until a separate real-host qualification proves all exclusion and teardown
obligations, `physical_lace=NOT_PROVEN`, `gate_b=BLOCKED`, `release=BLOCKED`.
