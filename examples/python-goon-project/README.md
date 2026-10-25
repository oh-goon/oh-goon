# Run a complete Python Goon example from Git

Classification: runnable-with-prerequisites

This selection fetches one explicit revision of this repository, captures the named Plan and its local Iris Goon project, and runs the installed `iris-train` command against an explicitly registered target. The Plan's relative import is confined to the selected Git authoring root. It cannot substitute an installed catalog package for the project at that revision.

Use the [headless registration guide](../../templates/README.md#first-local-python-run) to obtain the target's canonical repository ID. Copy this directory's `run.yml` into a fresh workflow directory, replace `REPOSITORY_ID`, and choose caller values there. The installed OH-Goon version must support Python Goon projects. Before this feature is merged, select the feature branch explicitly in `source.ref`.

```sh
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

Locking acquires the source and records its exact commit in `goon.lock`. Neither acquisition nor configuration inspection installs dependencies or executes project code. A subsequent Run consumes that pinned revision; changing the remote branch or losing the cache does not advance it. Use `oh-goon lock ./workflow/run.yml --update-goons` only when you want to refresh the selected Git pins. Cached inspection with `oh-goon config inspect ./workflow/run.yml` never fetches missing source.

This example deliberately retains `purpose: cicd` and content-store model artifacts. It requires the selected strict executor and retained-content publication prerequisites; ordinary `make init` does not qualify those guarantees. See the [Iris Goon](../../templates/goons/ml/iris-train/README.md) for native uv development without OH-Goon and the [CI/CD contract](../../docs/CI_CD_CONTRACT.md) for execution prerequisites. Use `python/quality` for the ordinary local first success.

After completion, inspect the printed Run ID through Game Tape or `oh-goon plan tape RUN_ID` and `oh-goon plan outputs RUN_ID`. Metrics retention records file facts; the model-parameter declaration retains retrievable bytes through the existing content store. This example is not evidence of APP certification or privileged native qualification on the current host.
