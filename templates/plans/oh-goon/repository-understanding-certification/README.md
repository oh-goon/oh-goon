# oh-goon/repository-understanding-certification

Repository-owned verification Plan for the agent comprehension exam.

## Use this Plan

```sh
oh-goon templates show oh-goon/repository-understanding-certification --kind plan
oh-goon plan inputs templates/plans/oh-goon/repository-understanding-certification/plan.yml
```

Copy with `oh-goon templates show oh-goon/repository-understanding-certification --kind plan > plan.yml`. Select caller inputs with one `--inputs-file` and optional `--input` overrides, then validate and lock that exact selection. The [developer journey](../../../README.md) shows the complete local Python flow; the [CI/CD guide](../../../../docs/CI_CD_CONTRACT.md) owns configured isolated execution. Append a Goon to the ordered list to extend this Plan; executable Steps stay in the Goon.

This is an internal Oh-Goon workload. Use this repository’s prepared checkout and its owning Make/certification command. Workspace-reference inputs must come from the selected producer; they are not host directory paths. Results belong to the original Run and Game Tape.

`make init` already owns dependency/interpreter materialization. This verification Plan consumes the initialized repository and runs `oh-goon/repository-exam`, which validates questions, grader answers, scoring metadata, and version-bound evidence references, then retains the exam manifest as evidence. Exact source ranges remain valid in this domain because source-location evidence is explicitly part of the Repository Understanding contract; those ranges do not become unrelated APP product authority.

This Plan deliberately stops before candidate-model execution. Oh-Goon does not currently have an Agent Step whose filesystem capability can make source read-only while excluding grader answers. Do not emulate that missing boundary by shelling out to an agent and relying on a prompt.

## Validate your copy

```sh
oh-goon plan validate plan.yml
oh-goon lock plan.yml
```
