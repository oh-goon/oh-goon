# agents/adjudicate

Account for every finding and propose a coherent repair queue.

**Status:** runnable with prerequisites. **Effects:** Sends admitted review evidence to the configured provider to account for findings.

**Prerequisites:** Prior review records, captured repository/adjudication Skill sources and an admitted model provider.

```sh
oh-goon templates init agents/agentic-review-repair-loop --kind plan --output ./workflow
```

The [agents/agentic-review-repair-loop Plan](../../../plans/agents/agentic-review-repair-loop/README.md) supplies the surrounding policy and ordered work. Set `target.repository_id` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Result:** A typed adjudication record accounting for the review findings.

Read-only Agent. Uses `adjudication_skill`, `repo`, `goal` and `acceptance`. Every finding is explicitly repaired or left alone with a rationale; secondary evidence remains attached to its original review. The native loop owner decides whether another phase may start.
