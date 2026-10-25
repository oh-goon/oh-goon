# ml/iris-train

Teaching example for a repository-provided `iris-train` command running from the locked `ml` dependency group.

**Status:** teaching example. **Effects:** Runs training/evaluation and writes metrics and model parameters.

**Prerequisites:** A repository-provided `iris-train` command in the locked `ml` group, an admitted strict executor and content-store publication for model-parameter bytes.

```sh
oh-goon templates init ml/iris-training --kind plan --output ./workflow
```

The [ml/iris-training Plan](../../../plans/ml/iris-training/README.md) supplies the surrounding policy and ordered work. Set `target.repository_id` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Expected outputs:** Metrics JSON is evidence; reusable model-parameter bytes have content-store retention.

The [Iris project fixture](../../../../template-certification/fixtures/ml/iris/) supplies a real `iris-train` command and native scikit-learn dependency in its locked `ml` group. It fits scaling and logistic regression on the training partition, evaluates held-out rows and writes both declared JSON artifacts. The command exits nonzero when actual accuracy misses the selected minimum. Change seed, split, accuracy and iteration inputs through caller values.

The project command owns the training program and writes the declared metrics and model-parameter artifacts. Oh Goon does not embed a Python program in YAML or create a second dependency description for scikit-learn.

**Artifact retention:** Metrics JSON is `evidence`; model parameters use `content_store` because their file bytes are the reusable training product. See the [canonical retention contract](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).

Select the strict `ml/iris-training` Plan for this retained product. A verification-purpose wrapper refuses this Goon's content-store retention before training; it does not discard the retention request or choose a weaker execution policy.
