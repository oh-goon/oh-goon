# ml/iris-train

Teaching example for a repository-provided `iris-train` command running from the locked `ml` dependency group.

**Status:** teaching example. **Effects:** Runs training/evaluation and writes metrics and model parameters.

**Prerequisites:** A repository-provided `iris-train` command in the locked `ml` group, an admitted strict executor and content-store publication for model-parameter bytes.

**What to edit / settings owner:** select caller values in your workflow `run.yml`. [`goon.yml`](goon.yml) owns input types/defaults, Steps, requirements, effects and outputs. Select this capability through a Plan; use [the common setup and customization guide](../../../README.md) for inspect → validate → lock → run.

**Expected outputs:** Metrics JSON is evidence; reusable model-parameter bytes have content-store retention.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

**Customize:** change caller inputs in `run.yml`; use ordinary `with:`/`pass:` bindings and ordered imports in a project Plan/Composite for structural changes. Native tools keep their own configuration.

Use the Iris workload under the existing template-certification fixture as the project-command reference. Its locked `ml` group supplies `iris-train`. Change seed, split, accuracy and iteration inputs through caller values. Metrics are evidence; durable model-parameter bytes use the configured content store.

The project command owns the training program and writes the declared metrics and model-parameter artifacts. Oh Goon does not embed a Python program in YAML or create a second dependency description for scikit-learn.

**Artifact retention:** Metrics JSON is `evidence`; model parameters use `content_store` because their file bytes are the reusable training product. See the [canonical retention contract](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).
