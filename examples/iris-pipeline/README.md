# Iris ingest → train → evaluate → publish

Classification: runnable-with-prerequisites

This trusted-local teaching workflow selects the **same** complete Python project at
[`templates/goons/ml/iris-train`](../../templates/goons/ml/iris-train/README.md).
Its separate `iris-pipeline` entrypoint composes native scikit-learn and joblib
across four ordinary Steps. The existing `iris-train` command and single-step
Goon/Plan remain available with their own contract.

Prerequisites are an initialized Oh-Goon CLI, native Git/uv and Python 3.12+.
Use a real registered repository containing the application. There is no
target-free execution or special ML runner. Native uv consumes the project's
committed lock and installs base dependencies in each owned environment.

From the Oh-Goon source checkout, select an actual application checkout and a
new workflow directory whose parent already exists:

```sh
iris_application=/absolute/path/to/iris-project
iris_repository=/absolute/path/to/its/repository
iris_workflow=/absolute/path/to/iris-workflow
oh-goon init --template python/quality --output "$iris_workflow" --repository "$iris_repository" --no-input
cp examples/iris-pipeline/plan.yml "$iris_workflow/plan.yml"
cp -R examples/iris-pipeline/goons "$iris_workflow/goons"
```

Keep the generated `target` in `run.yml`. Replace its `source` with
`source: {kind: file-plan, path: ./plan.yml}` and remove the initializer's
`inputs` mapping. Set `application.path` in the copied Goon to
`$iris_application`. The included `run.yml` illustrates the selection shape;
do not copy its repository placeholder over the generated target.

```sh
oh-goon workflow inspect "$iris_workflow/run.yml" --json
oh-goon workflow prepare "$iris_workflow/run.yml" --no-input
oh-goon workflow validate "$iris_workflow/run.yml" --json
oh-goon run "$iris_workflow/run.yml" --no-input --json
oh-goon runs tape RUN_ID --json
oh-goon runs outputs RUN_ID --json
```

Inspection reports the input defaults and declaring Goon. To change a seed or
accuracy threshold, edit caller `inputs` in `run.yml`, then prepare again.
The Plan does not duplicate the Goon's input schema. Each invocation admits a
new Run and enters the ordinary fresh-process worker and Step/Attempt owners.

`ingest` selects Iris data and a native train/test split once. `train` fits from
that DATA, and `evaluate` uses the transferred MODEL to measure held-out accuracy.
The JSON scalars are limited to 16 KiB and bound through the original output
owner. They are not arbitrary file paths or a claim of shared Step workspaces.
`model_packet_sha256` binds the canonical MODEL JSON, not the serialized joblib
file. The content store owns the exported file's SHA-256 and byte identity.
Packet digests establish self-consistency, not authenticated external model or
evaluation provenance. These tiny public Iris values may appear in command
arguments and recorded Run evidence; this is not a private or large-data intake.

Successful evaluation permits `publish` to validate the exact accepted verdict,
restore the native fitted pipeline, and write metrics, parameters and a joblib
model. With a threshold of 100 on the default split, evaluation fails and
publication is skipped; no newly published model is available from that Run.
Other native failures stop downstream Steps through the same existing policy.

Use the exact publish Step ID returned by `runs outputs` to export retained
bytes to new destinations:

```sh
mkdir -p ../iris-results
oh-goon runs export-artifact RUN_ID --step PUBLISH_STEP_ID --path ml-output/iris-training-metrics.json --output ../iris-results/metrics.json
oh-goon runs export-artifact RUN_ID --step PUBLISH_STEP_ID --path ml-output/iris-model-parameters.json --output ../iris-results/parameters.json
oh-goon runs export-artifact RUN_ID --step PUBLISH_STEP_ID --path ml-output/iris-model.joblib --output ../iris-results/model.joblib
```

Exports use retained exact-Run content after attempt scratch is disposed. Later
training or caller-source edits do not rewrite earlier evidence. A failed or
skipped publisher cannot authorize exporting an unrelated developer output.
Treat joblib files as executable Python serialization: reopen only artifacts
from a source and Run that you trust.

For the same application from Git, replace the Goon's `application` block with
an explicit origin, reviewed commit and contained project path, for example:

```yaml
application:
  git: https://github.com/oh-goon/oh-goon.git
  ref: COMMIT_CONTAINING_IRIS_PIPELINE
  path: templates/goons/ml/iris-train
```

Replace the commit placeholder with a real revision that contains the new
entrypoint. Prepare first to acquire that exact source, then inspect, validate
and run. Ordinary preparation retains the resolved Git pin; only an explicit
`workflow prepare ... --refresh applications` refreshes it. The Run still uses
the actual registered repository as its effect subject. The integration proof
uses the same application through a local pinned Git fixture; it does not
qualify a live remote host or private Git authentication.

The [native integration proof](../../tests/integration/test_iris_pipeline.py)
keeps a local success, evaluation failure, native application guards and invalid
binding in ordinary feedback. Its longer pinned-Git/seed/history and complete
failure-stage permutations retain daily cadence. Both use the copied
application/workflow through the installed source-checkout CLI and retain
native artifact and fresh-process history checks. This
does not establish strict custody, installed-release or WSL/container parity,
external dataset support or scheduled-training acceptance.
