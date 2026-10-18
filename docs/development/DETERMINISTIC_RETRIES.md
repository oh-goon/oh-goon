# Deterministic Trainer retries

> An extra physical execution must never appear because the runtime "thought another try might help."

Fleet Plan v30 introduced this retry model; current Fleet Plan v40 treats automatic retry as immutable admitted execution data. The default is one attempt.
When a step opts into replay, the plan must fully describe what completed failures are replay-safe and
exactly when each additional attempt may start.

## Authoring contract

```yaml
retry:
  max_attempts: 3
  known_transient_exit_codes: [75, 124]
  reason: "the contained local tool documents these completed failures as replay-safe"
  delays_seconds: [1, 3]
```

The contract is closed:

- `max_attempts` is 1–5;
- `known_transient_exit_codes` is explicit and unique;
- `reason` is mandatory whenever more than one attempt is allowed;
- `delays_seconds` has exactly `max_attempts - 1` entries;
- each delay is admitted before execution and is bounded to 0–300 seconds;
- retry codes cannot also be `success_codes`.

There is no `backoff: exponential`, random jitter, stderr classifier, exception classifier, or dynamic
"transient" detector. If the desired schedule is `1s, 3s, 9s`, the plan says `[1, 3, 9]`.

## Replay-safety boundary

Generic outer Trainer retry is allowed only when the step's admitted network egress mode is `none`.
Every attempt is created inside a fresh attempt-private boundary. That makes a completed local failure a
bounded replay of private work rather than an implicit replay of an external side effect.

A generic command with network authority is rejected when automatic retry is configured. A process can
return a known code after an HTTP request, registry operation, database write, or other remote effect has
already happened; an exit code alone cannot prove replay safety. Typed integrations may own their own
finite transport behavior only when the integration contract defines the request semantics and evidence.
That is separate from Trainer.

Trainer never replays:

- cancellation;
- raised/ambiguous process loss;
- unknown external-effect outcomes;
- repository-effect quarantine;
- an exit code not listed in `known_transient_exit_codes`;
- a failure after the admitted attempt budget is exhausted.

## Canonical decision evidence

A configured retry policy produces one structural decision for each known completed failed attempt:

- `SCHEDULED` — records exit code, admitted reason, exact delay, and next attempt number;
- `EXIT_CODE_NOT_DECLARED` — the failure is known, but this exit code was not admitted as transient;
- `ATTEMPTS_EXHAUSTED` — the exit code is admitted transient, but no further attempt remains.

No configured retry policy is represented by **absence** of retry evidence, not a synthetic state.

Fleet result validation proves the physical attempt chain. Attempt numbers must be consecutive from 1,
and every attempt after the first must be explained by exactly one `SCHEDULED` fact on the immediately
preceding attempt. A stored result cannot therefore contain an unexplained extra execution.

## Operator contract

Game Tape/Trainer may display the canonical decision, policy reason, exact delay, exit code, and next attempt.
The UI must not reconstruct retry intent from attempt count, duration, stdout/stderr, or a guessed error
category. The same evidence drives Runs and any future run-detail surfaces.

## Contract accounting

Fleet Plan **v30** changes authoring/runtime-significant retry semantics. Canonical operation evidence now
contains the retry decisions that explain physical replay, so the pre-live database is **epoch 51**.
Current contracts are Fleet Plan **v40**, database epoch **76**, executor inventory **v6**, Enforcer receipt **v18**, and external executor report **v3**. There is no compatibility
parser for the old retry shape and no epoch-50 durable reader.
