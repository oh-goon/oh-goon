# agents/repair

Propose a source-bound batch and apply it through an admitted native Step.

**Status:** runnable with prerequisites. **Effects:** Generates proposal data, then applies admitted changes through the ordinary apply Step.

**Prerequisites:** The review–repair Plan, captured repair Skill and model provider, explicit write roots and fresh repair authorization.

```sh
oh-goon init --template agents/agentic-review-repair-loop --kind plan --output ./workflow --draft
```

This explicit draft is NOT_READY and has no lock. Complete `target.repository` and required inputs or Agent/repair fields, then prepare it. For a complete first run, use [normal initialization](../../../README.md).

The [agents/agentic-review-repair-loop Plan](../../../plans/agents/agentic-review-repair-loop/README.md) supplies the surrounding policy and ordered work. Set `target.repository` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Result:** Proposal data and the native apply result. Later project checks own verification.

Uses `repair_skill`, `repo`, `goal` and `acceptance`. The Agent only proposes. The ordinary native `oh-goon agent apply` Step validates the entire batch before writing. `@admitted` and `@candidate` are replaced by the existing repair owner with the invocation's explicit write roots and captured candidate identity before Run admission. These markers never authorize standalone apply. Create, update and delete require exact preimages; publication failures stop as incomplete.
