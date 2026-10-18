# ml/iris-train

Teaching example for a repository-provided `iris-train` command running from the locked `ml` dependency group.

The project command owns the training program and writes the declared metrics and model-parameter artifacts. Oh Goon does not embed a Python program in YAML or create a second dependency description for scikit-learn.

**Artifact retention:** Metrics JSON is `evidence`; model parameters use `content_store` because their file bytes are the reusable training product. See the [canonical retention contract](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).
