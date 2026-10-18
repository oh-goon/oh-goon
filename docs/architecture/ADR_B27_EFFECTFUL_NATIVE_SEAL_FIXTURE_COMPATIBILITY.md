# ADR: B27 effectful native seal fixture compatibility

Status: implemented development test repair in `P1B27_GATE_P1P2A1`, not a security gate approval.

## Evidence / decision

Native WSL witness for exact `P1B27B2B3B2B3B` (14 October 2026) ran six original QENV1 rings; five passed, PHYSICAL_OBSERVER reported exactly one failure (`380/381` passing), overall exit 1. With CPython 3.14.7 the test tried `fcntl.F_ADD_SEALS` but the Python module omitted the constant. The fixture failed before a real Bubblewrap launch. The original production atomic execution bytes implementation already provides the Linux UAPI fallback in `_linux_seal_operations()`.

The existing opt-in native fixture now consumes that function, rather than duplicating constant values or introducing another sealer. A missing-Python-constants regression runs in the normal PHYSICAL_OBSERVER ring and verifies real sealed memfd operations on Linux. The original architecture guard forbids reintroduction of direct module constants in the fixture.

## Invariants

- One source of truth for Linux seal flag/operation compatibility in the original production module; no additional authority or runtime change.
- The requested real effectful test must fail before success if bwrap, `--bind-fd`, namespaces, kernel mountinfo, required mounted bytes or teardown quiescence cannot be observed.
- Hosted regressions are neither native physical evidence nor independent LACE/Gate B certification.
- A new exact-source native WSL run is mandatory; B3B is failed and remains failed.
