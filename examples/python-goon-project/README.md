# Run a complete Python Goon example from Git

Classification: runnable-with-prerequisites

This selection fetches one explicit revision of this repository, captures the named Plan and its local Iris Goon project, and runs the installed `iris-train` command against an explicitly registered target. The Plan's relative import is confined to the selected Git authoring root. It cannot substitute an installed catalog package for the project at that revision.

Use an initialized [Oh-Goon CLI](../../README.md#cli-first--no-frontend-setup)
and your actual development Git repository as the target. Initialize its identity
by path, then edit the generated `run.yml` to use this directory's explicit
`git-plan` source and choose caller values:

```sh
task_workflow=/absolute/path/to/iris-git-workflow
oh-goon init --template ml/iris-training --output "$task_workflow" --repository /absolute/path/to/your/project --no-input
```

The output directory must not exist and its parent must already exist. Copy
only the `source:` mapping from this example's [run.yml](run.yml); keep the
generated target and typed inputs. Its unique Goon import exposes the Iris
defaults without a copied Plan schema. Set `inputs.random_seed: 7` if you want
the caller choice shown in that file. Use an installed Oh-Goon version that
supports Python Goon projects.

```sh
oh-goon workflow prepare "$task_workflow/run.yml"
oh-goon run "$task_workflow/run.yml" --no-input
```

Preparation acquires the source and records its exact commit in `goon.lock`.
Neither acquisition nor configuration inspection installs dependencies or
executes project code. A subsequent Run consumes that pinned revision; changing
the remote branch or losing the cache does not advance it. Use
`oh-goon workflow prepare "$task_workflow/run.yml" --refresh goons` only when
you want to refresh the selected Git pins. Optional inspection with
`oh-goon workflow inspect "$task_workflow/run.yml"` uses existing source and
never fetches missing source; `--acquire` explicitly permits acquisition before
preparation. `workflow validate` checks the same selected configuration without
executing training.

This example explicitly selects `purpose: cicd`. It requires an admitted strict
executor; ordinary `make init` does not qualify those guarantees. For trusted
local training with retained metrics and model parameters, keep the initialized
catalog `ml/iris-training` source instead. See the
[Iris Goon](../../templates/goons/ml/iris-train/README.md) for native uv development
and the [CI/CD contract](../../docs/CI_CD_CONTRACT.md) for strict prerequisites.

After completion, inspect the printed Run ID through Game Tape or
`oh-goon runs tape RUN_ID` and `oh-goon runs outputs RUN_ID`. Both JSON artifact
declarations retain retrievable bytes through the existing content store. Use
the [export and comparison commands](../../templates/plans/ml/iris-training/README.md#retrieve-and-compare-results)
with this Run ID. This example is not evidence of APP certification or privileged
native qualification on the current host.
