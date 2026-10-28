# Python CI through the original Oh-Goon Run

Classification: runnable-with-prerequisites

This example lints, tests and builds the existing
[Orders Python application](../../templates/goons/general/orders-taskflow/README.md).
GitHub Actions or GitLab CI triggers the job. The public Oh-Goon CLI locks and
admits one Plan, executes its Goons, waits for completion and reports the result.
Native uv, Ruff, pytest and the package build backend own their operations.

The [Plan](plan.yml) imports two small Goons:

| Goon | Native work | Recorded output |
| --- | --- | --- |
| [python/checks](goons/python/checks/README.md) | Install the selected application's locked `ci` group, run Ruff, then pytest | Command results and retained Ruff JSON/JUnit diagnostics |
| [python/wheel](goons/python/wheel/README.md) | `uv build --wheel --out-dir dist` against the captured application | Retained wheel bytes, raw SHA-256 and size |

Both select the same application at the Plan's Git revision. Checks use its
installed Python runtime; the build invokes native uv directly. A failed lint,
test or dependency preparation prevents later Steps from running. Ordinary
verification uses trusted local execution; this example does not grant strict
isolation, APP certification or enforced CPU/memory caps.

## Run this checkout locally

Use Linux with Bash, Git, Make, Python 3.12+ and uv. Prepare the CLI from this
repository's committed backend lock; no frontend setup is needed:

```bash
make backend-install
```

Commit the application and recipe changes you intend to execute. Run selects
the workload repository's exact `HEAD`, so an uncommitted edit is not selected
source. Keep runtime state outside the workload checkout:

```bash
task_tool="$PWD"
task_workload="$PWD"
task_job=$(mktemp -d)
bash examples/python-ci/run.sh run "$task_tool" "$task_workload" \
  examples/python-ci/plan.yml "$task_job/state"
bash examples/python-ci/run.sh collect "$task_tool" "$task_job/state" "$task_job/results"
```

`run` returns the original CLI exit code: 0 for success, 1 for failed work and
2 for a rejected invocation. A handled interrupt returns 130 for SIGINT or 143
for SIGTERM. CI runs `collect` in its separate always-run/after-script phase so
a failing Run still has inspectable results without hiding its failure.

The adapter registers this actual application repository, obtains its ID from
the public inventory, writes a `git-plan` selection for the exact commit and
calls `lock` then waited `run`. It does not copy database IDs by hand, create an
empty target repository or implement another execution loop. State must be a
new directory. Tool installation, state and caches stay outside the captured
application; managed execution leaves the developer environment and checkout
untouched.

## Copy into your own project

Copy this directory to `ci/oh-goon/` in your Python Git repository. Change these
project values and commit the result:

1. In both Goon YAML files, set `application.path` to the project directory
   relative to the repository root, for example `.`. This is the Git Plan's
   authoring root, not the directory containing the Goon.
2. In the checks Goon, select your native dependency group and commands. Your
   committed `pyproject.toml` and `uv.lock` must include the chosen lint/test
   tools. The Orders example's `ci` group includes pytest and Ruff; your project
   owns its lint rules and test configuration.
3. In the wheel Goon, declare the exact wheel filename produced by your project.
   Change that declaration when the distribution name or version changes.
4. Copy [github-actions.yml](github-actions.yml) to
   `.github/workflows/python-ci.yml`, or use [gitlab-ci.yml](gitlab-ci.yml) as
   `.gitlab-ci.yml`. Set the Plan path to `ci/oh-goon/plan.yml`, the adapter path
   to `ci/oh-goon/run.sh`, and select the Oh-Goon source revision you intend to
   install. Pin a reviewed full commit SHA for repeatable tool installation.

The hosted examples install the CLI in a separate checkout and keep one fresh
state directory per job. They use backend-only native uv setup and read-only
source access. Supply your own repository credentials through the platform if
your selected sources require authentication. The GitLab example expects a
Linux shell runner with the documented tools; it does not configure a runner.

Run `uv lock` intentionally after changing application dependencies and commit
the resulting native lock. The checks Goon requests `lock_mode: locked`: native
uv must accept current project metadata without rewriting the lock. Native
`uv build` owns build dependency resolution; it has no `--locked` option. Each
new job locks the selected application/recipe commit into `goon.lock`; execution
does not silently refresh it. An earlier job's lock and results remain tied to
their original commit.

## Collect and read the result

On a terminal Run, `collect` uses public `plan status`, `plan tape`,
`plan outputs` and `plan export-artifact` commands. The promoted directory
contains:

- `run.json`, `status.json`, `tape.json` and `outputs.json`: the original Run ID,
  outcome, selected source and per-Step command, exit and artifact facts.
- `goon.lock`: the selected workflow/application source pins.
- `artifacts/` and `exports.json`: verified retained wheel bytes and the public export
  receipts identifying their Run, repository, Step, destination, raw hash and
  size. Each exported file has its own directory, including files whose names
  match another Step's artifact or a portable receipt.
- `toolchain.json`: observed CLI source revision, dirty flag and native tool
  versions. These observations are diagnostics; the original Run evidence owns
  the execution result.

The wheel can be installed with native uv into an independent environment.
Ruff JSON and JUnit XML are explicitly retained because CI readers need their
diagnostics after attempt disposal, including reports produced by a failed
command. These files use the same collector and export path as the wheel.
Raw process logs retain their existing transient availability.

The hosted artifact upload preserves the promoted files after the disposable
runner disappears. Read the JSON with an ordinary JSON reader and verify the
wheel against the recorded raw SHA-256. The uploaded directory is not a live
Oh-Goon database. CLI history and later re-export require the original state
and live retained reference; see the
[retention lifecycle](../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).
Locally, you can run `collect` again with the same state and a new destination,
including after the original workload checkout is removed.

If lint or tests fail, their original exit/result and skipped build remain in
the recorded Run, and no wheel is exported. Open the exported Ruff/JUnit report
for the native diagnostic. If dependency preparation fails before a command
starts, no report from that command is available. If source or lock admission fails
before a Run is submitted, collection reports that there is no submitted Run
receipt. It does not manufacture a successful empty execution. Review the
original CLI refusal and recorded Step preparation facts for the responsible
native operation or configuration. Collection also fails if a successful Run
has no retained wheel, even when diagnostic reports were exported; check that
the declared filename matches the native build output.

## Cancellation and qualification

The GitHub Run command uses `exec`, making the waited CLI the script's entry
process. GitLab's shell executor delivers native signals to the job process
group; its script also makes successful collection part of the job result.
The existing Run service handles cancellation; no shell trap launches a second
cancellation controller. A handled SIGINT/SIGTERM requests the original
Run's cancellation and emits an interrupted receipt. Durable readback, rather
than the signal alone, establishes process settlement and workspace cleanup.
Forced termination may prevent both cleanup and artifact collection.
[GitHub's cancellation sequence](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-cancellation)
and the [GitLab shell executor](https://docs.gitlab.com/runner/executors/shell/)
own their signal delivery and final process termination.

The [GitHub qualification workflow](../../.github/workflows/python-ci-example.yml)
executes this recipe against its exact checked-out commit and uploads its
portable results. The native acceptance tests in
`backend/tests/integration/services/test_ci_python_example.py` exercise successful
builds at two commits, installed wheel behavior, failure propagation, stale uv
metadata refusal and independent historical collection. Separate native CLI
tests exercise SIGINT/SIGTERM, terminal cancellation, child-process exit and
scratch removal. A green hosted success does not qualify whole-job cancellation
or the GitLab hosting environment; those require their own observed runs.

The [TaskFlow walkthrough](../airflow-taskflow-showcase/README.md) uses this same
application's business operations and retained JSON result.
