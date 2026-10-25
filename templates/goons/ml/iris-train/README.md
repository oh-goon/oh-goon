# ml/iris-train

Teaching example for a repository-provided `iris-train` command running from the locked `ml` dependency group.

**Status:** teaching example. **Effects:** Runs training/evaluation and writes metrics and model parameters.

**Prerequisites:** A repository-provided `iris-train` command in the locked `ml` group, an admitted strict executor and content-store publication for model-parameter bytes.

```sh
oh-goon templates init ml/iris-training --kind plan --output ./workflow
```

The [ml/iris-training Plan](../../../plans/ml/iris-training/README.md) supplies the surrounding policy and ordered work. Set `target.repository_id` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Expected outputs:** Metrics JSON is evidence; reusable model-parameter bytes have content-store retention.

The template-certification Iris fixture supplies an `iris-train` argument stub in its locked `ml` group for startup/admission checks. It does not train a model or produce the declared artifacts. Provide a real project command before running this teaching workflow. Change seed, split, accuracy and iteration inputs through caller values.

The project command owns the training program and writes the declared metrics and model-parameter artifacts. Oh Goon does not embed a Python program in YAML or create a second dependency description for scikit-learn.

**Artifact retention:** Metrics JSON is `evidence`; model parameters use `content_store` because their file bytes are the reusable training product. See the [canonical retention contract](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).
