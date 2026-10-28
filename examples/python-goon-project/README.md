# Run a complete Python Goon example from Git

Classification: runnable-with-prerequisites

This selection fetches one explicit revision of this repository, captures the named Plan and its local Iris Goon project, and runs the installed `iris-train` command against an explicitly registered target. The Plan's relative import is confined to the selected Git authoring root. It cannot substitute an installed catalog package for the project at that revision.

Use your actual development repository as the target. Initialize its identity
by path, then edit the generated `run.yml` to use this directory's explicit
`git-plan` source and choose caller values:

```sh
oh-goon league add-folder /absolute/path/to/your/project
oh-goon templates init ml/iris-training --kind plan --output ./workflow --repository /absolute/path/to/your/project
```

Copy only the `source:` mapping from this example's `run.yml`; keep the generated
target identity. Use an installed OH-Goon version that supports Python Goon projects.

```sh
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

Locking acquires the source and records its exact commit in `goon.lock`. Neither acquisition nor configuration inspection installs dependencies or executes project code. A subsequent Run consumes that pinned revision; changing the remote branch or losing the cache does not advance it. Use `oh-goon lock ./workflow/run.yml --update-goons` only when you want to refresh the selected Git pins. Cached inspection with `oh-goon config inspect ./workflow/run.yml` never fetches missing source.

This example explicitly selects `purpose: cicd`. It requires an admitted strict
executor; ordinary `make init` does not qualify those guarantees. For trusted
local training with retained metrics and model parameters, keep the initialized
catalog `ml/iris-training` source instead. See the
[Iris Goon](../../templates/goons/ml/iris-train/README.md) for native uv development
and the [CI/CD contract](../../docs/CI_CD_CONTRACT.md) for strict prerequisites.

After completion, inspect the printed Run ID through Game Tape or
`oh-goon plan tape RUN_ID` and `oh-goon plan outputs RUN_ID`. Both JSON artifact
declarations retain retrievable bytes through the existing content store. Use
the [export and comparison commands](../../templates/plans/ml/iris-training/README.md#retrieve-and-compare-results)
with this Run ID. This example is not evidence of APP certification or privileged
native qualification on the current host.
