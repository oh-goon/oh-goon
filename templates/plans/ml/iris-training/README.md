# ml/iris-training

Run the project’s Iris training command with configurable training and evaluation inputs.

**Teaching example. Not a production MLOps template.**

## Use this Plan

```sh
oh-goon templates show ml/iris-training --kind plan
oh-goon plan inputs templates/plans/ml/iris-training/plan.yml
```

Copy with `oh-goon templates show ml/iris-training --kind plan > plan.yml`. Select caller inputs with one `--inputs-file` and optional `--input` overrides, then validate and lock that exact selection. The [developer journey](../../../README.md) shows the complete local Python flow; the [CI/CD guide](../../../../docs/CI_CD_CONTRACT.md) owns configured isolated execution. Append a Goon to the ordered list to extend this Plan; executable Steps stay in the Goon.

Use the Iris workload under the existing template-certification fixture as the project-command reference. Its locked `ml` group supplies `iris-train`. Change seed, split, accuracy and iteration inputs through caller values. Metrics are evidence; durable model-parameter bytes use the configured content store.

This Plan intentionally does one thing: select `ml/iris-train` and expose its deterministic training/evaluation inputs at the run boundary. It contains no executable Steps.

Use it to learn the Oh Goon authoring model for ML work:

```text
Plan ml/iris-training
  → Goon ml/iris-train
      → lock check
      → explicit dependency cache acquisition
      → offline deterministic train/evaluate
```

The Plan intentionally leaves CPU/memory/process caps unspecified; effective capacity comes from the admitted executor and remains visible in resolved Plan/admission evidence. See the Goon README for repository requirements, outputs, and the production ML concerns intentionally omitted from this example.

## Configure a copy

Save these training choices as `values.yml`. The native project command evaluates accuracy and returns the actual result.

```yaml
inputs:
  random_seed: '42'
  test_size_percent: '20'
  minimum_accuracy_percent: '90'
  max_iterations: '300'
```

Validate and lock the same selection:

```sh
oh-goon plan validate plan.yml --inputs-file values.yml
oh-goon lock plan.yml --inputs-file values.yml
```

Use the same file and any explicit `--input` overrides when running. An override also needs a matching refreshed lock. Inspect the returned Run with `plan status`, `plan steps` and `plan outputs`.
