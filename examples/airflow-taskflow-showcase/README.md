# Run the orders TaskFlow example from Git

Classification: runnable-with-prerequisites

Select one revision of this repository and run its complete [general/orders-taskflow Python Goon](../../templates/goons/general/orders-taskflow/README.md) against an empty registered target. This ordinary trusted verification Plan needs an installed OH-Goon CLI, Python 3.12+ and native uv. The application has no runtime dependencies; the separate Airflow adapter is optional.

Create and register a target without copying application code into it:

```bash
mkdir -p ./repositories/orders-target
git init ./repositories/orders-target
oh-goon league add-folder "$PWD/repositories/orders-target"
oh-goon league scan
oh-goon templates init general/orders-taskflow --kind goon --output ./workflow --repository "$PWD/repositories/orders-target"
```

Initialization binds the registered target and writes its canonical ID. Keep the generated `target` and `inputs` in `workflow/run.yml`; replace only its `source` mapping with this explicit Git selection:

```yaml
source:
  kind: git-plan
  git: https://github.com/oh-goon/oh-goon.git
  ref: main
  path: .
  plan: examples/airflow-taskflow-showcase/plan.yml
```

The selector names the repository, requested ref, authoring root and contained Plan. That Plan imports this revision's package explicitly; the installed catalog cannot shadow it. The initializer's local `workflow/plan.yml` is now unselected and can be removed after this edit. This directory's `run.yml` is a reference selection with a placeholder target; initialization supplies the actual target authority.

```bash
oh-goon config inspect ./workflow/run.yml --acquire
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml --json
```

Explicit inspection acquires source and explains the input contract and selected commit. It does not install or execute application code. Locking records the commit in `workflow/goon.lock`; later Runs retain that exact pin. Change a caller input and lock again. Only `oh-goon lock ./workflow/run.yml --update-goons` refreshes Git source selection. Cached inspection without `--acquire` does not fetch missing source.

The ordinary Run waits for completion. Use its returned `fleet_run_id` in a fresh CLI process:

```bash
oh-goon plan tape RUN_ID --json
oh-goon plan outputs RUN_ID --json
```

With `fixture: official`, all three Steps pass and the final artifact hash describes `{"total_order_value": "1236.70"}` followed by a newline. `fixture: changed` produces `60.00`. `fixture: invalid` passes extraction, fails transformation and skips loading; the Run requires attention. Each execution uses owned source, environment and output roots, leaving the empty target and developer environment unchanged.

The artifact uses `retention: evidence`: Game Tape preserves its hash, size and presence across process restart. It does not retain downloadable bytes. The Iris CI/CD [Git example](../python-goon-project/README.md) demonstrates a separate workflow requiring retained-content publication and the selected CI/CD executor.

OH-Goon executes the package's three ordered Steps and explicit output bindings. It does not execute or translate Airflow DAGs. The [canonical package guide](../../templates/goons/general/orders-taskflow/README.md) identifies the official Airflow 3.3.2 tutorial and this application's adaptations, and documents direct frozen Python development, the full business command and individual operations. Required CI runs the ordinary application tests under the native lock without Airflow. The optional external Airflow adapter and any scheduler/worker deployment remain separate qualification.
