# Frozen local-search workflow

Classification: runnable-with-prerequisites

Search the captured sample repository for `needle` through the original Step supervisor and Hashmarks evidence owner. One selected [`run.yml`](run.yml) drives source capture, locking, Fleet admission, native execution and durable Game Tape. This recipient-free tool needs no model or native-agent selection.

Use an initialized Oh-Goon installation with native Git. Copy this complete directory outside the Oh-Goon checkout, retaining its Plan, local Goon, repository and Skill. Initialize and register the sample repository:

```sh
git init ./repos/team/app
oh-goon league add-folder /absolute/path/to/local-search/repos
oh-goon league scan --json
```

Edit the included `run.yml`: set the registered sample repository's `target.repository` and set `agent.repository_root` to the absolute path of the copied `repos` directory. Keep `agent.skill_root: skills`; it is relative to `run.yml`. `inputs.repo`, `inputs.skill` and `agent.acceptance` select the existing captured-source contract.

```sh
oh-goon workflow inspect ./run.yml
oh-goon workflow validate ./run.yml
oh-goon workflow prepare ./run.yml
oh-goon run ./run.yml --json
oh-goon runs tape RUN_ID --json
```

The Plan imports the project-owned Goon with `from: ./goons`; its unique inputs project from that Goon. The search reads frozen repository bytes, reports evidence for the captured identity and preserves native failure/cancellation results. It does not claim that an absent match proves repository-wide absence. The small `SKILL.md` is product test data; reusable Skills belong to [agentsCookbook](https://github.com/Hugloss/agentsCookbook).

The [canonical example regression](../../backend/tests/integration/services/test_example_workflows.py) copies the actual configuration and executes the installed CLI through Fleet and Game Tape. It also proves that an ambient provider catalog receives no request. Supporting native tests use the checked-in source bytes and cover frozen delivery, no-match, cancellation, forbidden effects and tampered packets:

```sh
uv run --project backend --offline --frozen --no-sync pytest backend/tests/integration/test_agent01a_frozen_local_search.py
```

Preparation alone remains source/Step prework; the canonical Run supplies admission and durable results. See [Agent Skills](../../docs/AGENT_SKILLS.md) for the source and execution boundaries.
