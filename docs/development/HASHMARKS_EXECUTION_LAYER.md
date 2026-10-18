# Hashmarks repository-evidence → Oh-Goon execution handoff

The frozen product boundary is:

- **Hashmarks = repository-evidence plane.**
- **Coding agent = solution plane.**
- **Oh-Goon = execution and certification control plane.**

Hashmarks describes what repository evidence says matters. The coding agent decides what change to make. Oh-Goon decides whether, where, and how admitted work may run and whether the resulting execution evidence is certifiable.

## Ownership

Hashmarks owns repository identity/freshness, CodeMap relationships, static cross-project topology, impact/provenance, flat verification selection membership, repository-negative evidence, and repository-evidence provenance. Physical grouping/process-isolation hints are not admitted as execution authority.

Oh-Goon owns process launch and process-tree cleanup, deadlines, bounded concurrency, retry/resume admission, environment recovery, Fleet scheduling, execution/refinement grouping, result authority, execution provenance, certification provenance, Repository Understanding qualification, certificates, and Game Tape.

A thin Oh-Goon adapter may consume Hashmarks evidence. It must never call Hashmarks execution-loop helpers such as `execute_shard`, `run_next`, or `run_warmup`, and it must never build a second CodeMap/impact/verification-selection engine.

## Current admitted contract

Hashmarks is selected as an ordinary released Python dependency by `backend/pyproject.toml`; native `uv.lock` owns the exact selected artifact and `backend/.venv` owns materialization. Oh-Goon does not vendor, reacquire, repackage, or privately inject a Hashmarks release.

`oh_goon.integrations.hashmarks` is the single Python integration boundary. Hashmarks evidence is validated through supported producer/consumer contracts and bound to the producer implementation identity reported by the actually loaded Hashmarks runtime. The source tree does not carry a copied "current Hashmarks version/commit/tree/wheel" authority.

The producer supplies exact flat verification membership and repository/evidence provenance; `execution_layout=external` keeps physical grouping, workers, isolation and recovery exclusively Oh-Goon-owned. Artifact identity remains caller/admission provenance when supplied, while producer compatibility is determined by the loaded implementation identity.

Hashmarks standalone CLI/MCP installation is a separate external host concern owned by Hashmarks and the host application. The Oh-Goon backend consumes the Python API through native uv materialization and never launches the standalone CLI as a second dependency path.

Producer-native validation owns Hashmarks schemas, member ordering, duplicate
membership rules, and producer/member/envelope/contract identity generation.
After that validation, Oh-Goon checks its own admission bindings: the loaded
implementation, admitted repository and source, freshness, repository-evidence
scope, and external execution ownership. Safe structured pytest projection and
duplicate executable-target rejection belong to Oh-Goon execution; they do not
justify copying Hashmarks validation or recomputing producer identities.

The admitted `repository_provenance_identity` is carried unchanged into
execution-plan references and the terminal receipt. Regrouping may change an
Oh-Goon work identity, but it cannot change admitted producer provenance or
membership. Terminal accounting must partition that membership exactly once,
including failure, cancellation, timeout, and unexecuted remainder.

Historical interoperability series remain audit evidence for their original
sources. They neither select the current dependency nor create a compatibility
fallback. Offline terminal-evidence verification inspects a retained result;
it cannot promote that result into admission for new work.

## No execution-policy smuggling

The v1 envelope is repository evidence only. Oh-Goon admits the exact contract shape and rejects extra policy fields. The envelope does not control:

- argv/command construction;
- timeout/deadline;
- retry count or delay;
- workers/concurrency;
- schedule/machine placement;
- process lifecycle;
- environment recovery;
- result authority.

Those remain Oh-Goon-owned inputs.

## Execution grouping and isolation

Hashmarks owns selected membership, not physical shards. Oh-Goon applies its own explicit grouping policy and process/isolation authority after admission. Producer fields such as `preferred_granularity`, `may_regroup`, `isolated_process`, worker hints, or physical shard layouts cannot gain authority through the Hashmarks contract.

Oh-Goon may refine execution grouping after a timeout only if the exact admitted membership is conserved:

```text
union(children) == parent membership
no omitted node
no duplicate node
no added node
```

Refinement is owned by the current Oh-Goon execution/recovery path. Any refinement implementation must conserve the admitted parent membership exactly; there is no retained adapter for an older Hashmarks shard-layout contract.

## Resume evidence

Only PASS results are resumable. Evidence schema remains `oh-goon.shard-pass-evidence/v1`, but the evidence/key now binds the material repository-evidence inputs as well as execution work:

- admitted repository generation identity;
- Hashmarks inner plan identity;
- Hashmarks outer repository-evidence/envelope identity when present;
- Hashmarks repository content identity when present;
- Hashmarks selection-input identity when present;
- producer artifact and implementation identity when present;
- producer algorithm/version and source schema;
- shard ID and work identity;

Therefore identical node membership from a different envelope or producer artifact cannot reuse prior PASS evidence silently.

## CI scheduling

There is deliberately no `HashmarksSchedule`, background polling loop, or second scheduler. Scheduled execution remains an ordinary Oh-Goon Fleet Plan/Schedule occurrence.

## Consumer boundary

The live interoperability surface is the consumer-conformance contract supplied by the selected, loaded Hashmarks dependency. Oh-Goon admits the exact flat membership and then constructs its own `VerificationExecutionGroupingPolicy` and execution plan. Offline proof verification inspects existing terminal evidence only; it is not proof reuse and cannot admit an old proof as a new execution.

Hashmarks selects membership and repository evidence. Oh-Goon chooses whether, when, where, and under what execution/certification policy it runs.
