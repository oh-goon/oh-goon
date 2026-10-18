# oh-goon/self-verification

Canonical fast self-verification Plan for one exact initialized Oh-Goon source revision.

## Use this Plan

```sh
oh-goon templates show oh-goon/self-verification --kind plan
oh-goon plan inputs templates/plans/oh-goon/self-verification/plan.yml
```

Copy with `oh-goon templates show oh-goon/self-verification --kind plan > plan.yml`. Select caller inputs with one `--inputs-file` and optional `--input` overrides, then validate and lock that exact selection. The [developer journey](../../../README.md) shows the complete local Python flow; the [CI/CD guide](../../../../docs/CI_CD_CONTRACT.md) owns configured isolated execution. Append a Goon to the ordered list to extend this Plan; executable Steps stay in the Goon.

This is an internal Oh-Goon workload. Use this repository’s prepared checkout and its owning Make/certification command. Workspace-reference inputs must come from the selected producer; they are not host directory paths. Results belong to the original Run and Game Tape.

This is a normal verification Plan, not a privileged verifier. `make init` owns dependency/interpreter materialization once. Self verification consumes that initialized repository and must not reacquire dependencies. Plan resolution, locking, execution evidence, cancellation and Game Tape remain normal Oh-Goon machinery.

The fast boundary covers P0 execution integrity, repository tooling, semantic architecture ownership, generated contracts, integration behavior, and repository-understanding certification. Full `oh-goon self certify` selects the lean `oh-goon/app-certification` Plan; `make verify` owns broader diagnostics.

## Validate your copy

```sh
oh-goon plan validate plan.yml
oh-goon lock plan.yml
```
