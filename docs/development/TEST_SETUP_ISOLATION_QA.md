# Test setup isolation QA

Oh-Goon does not treat repeated fixture setup as waste by default. Repetition is desirable when it is the mechanism that prevents state, environment, identity, or authority from leaking between tests.

The machine-readable owner list is `docs/development/TEST_SETUP_ISOLATION_QA.yml`.

## Rule

Keep isolation fixtures function-scoped unless a proposed wider scope proves all of the following:

1. no state leaks across test ordering, failure, retry, or mutation;
2. the same isolation/authority boundary remains true;
3. an adversarial regression demonstrates the old leakage risk stays closed; and
4. re-measurement shows a meaningful benefit rather than a micro-optimisation.

A tiny reduction in fixture calls is not sufficient reason to trade away independent test state. The current process-identity, certification-frontdoor, and package-slim fixtures intentionally remain function-scoped.

Native [worker qualification](../../scripts/tests/test_pytest_worker_isolation.py)
reuses resource basenames across independent pytest processes to prove fixture
ownership, coverage union and cleanup. Caller HOME/XDG selection is preserved;
fixtures own their temporary state and restore environment changes. The existing
command supervisor handles cancellation and releases owned process resources.
Record actual admitted workers and exact membership for fresh/resumed runs;
unsupported host cells remain unqualified.
