# ADR — AGENT-01B3H: preserve failed read evidence and qualify real Skill loop

Status: development candidate, **not a release or native qualification PASS**.

## Problem

The original Agent Step review loop allows a ripgrep exit status of 1 because it
means an ordinary search found no matches. In the previously shipped `read_file`
path, that allowance unintentionally also applied to `head -c 2048` failures.
A model could then finish and the original Game Tape could record success for a
failed physical read. There was no focused regression for this distinction.

The native qualification entrypoint covered a one-search review and an
installed-model Skill-loop invocation that only had to read one repository
file. That does not validate the four-action Skill/resource/repository workflow.

## Decision and owner boundaries

* **Original Fleet Step execution** alone classifies a physical `read_file` exit
  1 as failed execution. The physical exit status is carried in stderr and the
  Step returns the existing `PLAN_CONTRACT_RETURN_CODE` (125) so the original
  plan/Game Tape status cannot reinterpret it as a harmless search no-match.
  No new result interpreter or process runner exists.
* **`rg` fixed-string search** may still return 1 as a legitimate no-match.
  Original selected harness permissions, budgets and source preparation remain
  unchanged.
* A new **native-only integration test** requests four distinct read-only
  actions, including captured Skill-resource inspection and repository-file
  inspection. It delegates tool calls to the **original** `_execute_command_step`
  while recording their argv in a test-only list. No subprocess, Ollama model,
  Hashmarks implementation or database is substituted. The expected action
  order, original Fleet admission, terminal status, and reopened SQLite/Game
  Tape evidence identity must all pass. A model failing that JSON protocol is
  unqualified, not a hosted-model PASS.
* `scripts/qualify_agent_local_model.sh` retains the existing single local
  model, locked `backend/uv.lock` including mandatory Hashmarks, real Ollama,
  original Fleet and SQLite ownership. The script executes the existing native
  tests **and** the four-stage H native test. It does not pull a model or
  bypass missing dependencies.

## Verification boundary

A hosted deterministic-model regression first reproduced the failed-read
success. The corrected production branch now fails that test's read attempt
before a final model evaluation can run.

Hosted tests exercise real `rg`/`head` processes and original Fleet/Game Tape,
but use deterministic local model replies and a substituted Hashmarks identity
leaf where the real wheel is unavailable. The native test is skipped unless a
real installed Ollama model and pinned Hashmarks runtime are present. On an
actual Linux/WSL host run:

```bash
bash scripts/qualify_agent_local_model.sh <installed-ollama-model>
```

No high-security signer, privileged containment proof, or advanced host
certification is introduced or made mandatory for standard trusted execution.
