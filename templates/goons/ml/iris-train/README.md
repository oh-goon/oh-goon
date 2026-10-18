# ml/iris-train

Teaching example for a repository-provided `iris-train` command running from the locked `ml` dependency group.

## Use this Goon

```sh
oh-goon templates show ml/iris-train --kind goon
oh-goon plan inputs templates/goons/ml/iris-train/goon.yml
```

Use `goons: [ml/iris-train]` in a Plan to keep the Goon defaults. Unique public inputs are available to caller values automatically. Use `with:` for fixed values, renamed inputs or producer references; route shared names explicitly with `pass:`/`with:`. A Composite Goon declares which child inputs it exposes. The [authoring guide](../../../README.md) shows values files, explicit local sources and how to append Steps to your own Atomic Goon.

Use the Iris workload under the existing template-certification fixture as the project-command reference. Its locked `ml` group supplies `iris-train`. Change seed, split, accuracy and iteration inputs through caller values. Metrics are evidence; durable model-parameter bytes use the configured content store.

The project command owns the training program and writes the declared metrics and model-parameter artifacts. Oh Goon does not embed a Python program in YAML or create a second dependency description for scikit-learn.

**Artifact retention:** Metrics JSON is `evidence`; model parameters use `content_store` because their file bytes are the reusable training product. See the [canonical retention contract](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).
