# ADR — Run enrollment is checked at the original Plan service boundary

**Decision:** Check original Fleet Run enrollment before source work in both
`FleetPlanService.run` and `FleetPlanService.run_reviewed`. The existing
CLI/HTTP preflight remains for the earlier caller-input byte boundary.

**Reason:** A Python/service caller can bypass entrypoint-specific preflight.
That caller must not resolve a Plan, read its lock or perform digest work
before passing the same original enrollment requirement. A preview or lock
digest is a source identity, not permission to execute.

**Owner:** FleetPlanService delegates to FleetRunControlPlane and the existing
FleetRunLifecycle. This is a membership-only, read-only preflight; final
Enforcer authorization and Fleet admission still occur separately after the
immutable target is resolved. No second token, signer, trust decision,
executor, journal or LACE physical custodian is introduced.

**Negative proof:** Both direct entrypoints deny missing, unknown and
unprovisioned identities before Plan resolution; an identity revoked between
CLI and service checks is refused at the service cut. Positive proof compiles
real checked-in Goon fixtures and verifies the exact `goon.lock` digest and
single final admission invocation. Physical admission is explicitly stubbed.

**Limits:** Hosted tests do not qualify independently enforced kernel custody
or release. The original `pyproject.toml`/`uv.lock` remain unmodified.
