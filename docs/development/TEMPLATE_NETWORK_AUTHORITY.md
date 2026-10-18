# Template network authority — explicit capability, not ambient permission

This is the canonical authoring explanation for network/egress requirements in
shipped Goons. The existing `PlanStepCapabilities.egress` model and production
Fleet resolver/admission/executor are the machine authority. README prose
explains the *reason* for a capability; it cannot grant that capability.

## The boundary

The default for every Step is `capabilities.egress.mode: none`: an executable,
a declared extra tool, or a dependency group does **not** imply network access.
Use an explicit `steps[].capabilities.egress` only when the operation genuinely
requires a destination outside the sandbox. The available modes are:

| Mode | Authority | Authoring decision |
| --- | --- | --- |
| `none` | No network namespace access for the Step | Default for offline, reproducible and verification-only work |
| `hostname_allowlist` | Named hostnames resolved and captured before effects, then restricted to frozen IPv4 answers, ports and protocols | Prefer when a stable provider hostname set is proven and backend support is available |
| `cidr_allowlist` | Explicit canonical IPv4 destination CIDRs, ports and protocols | Prefer for fixed private endpoints or a stable explicitly owned address range |
| `host_unrestricted` | Host networking without destination filtering | Last resort; requires documented semantic justification and risk acceptance |

`network: true` is a *supported simple authoring alias* for host-unrestricted
access, but must not be used by shipped production templates: the explicit
`capabilities.egress.mode` makes the breadth inspectable during review. Never
invent a hostname policy based on a transient DNS capture, pin arbitrary CDN
IPs, or silently select a narrower policy without testing the native command.
An allowlist with inaccessible endpoints is not a security improvement if it
makes an intended scan impossible. The executor must be able to enforce the
selected mode; environment/backend incompatibility is an admission failure,
not permission to broaden access.

## Shipped unrestricted exceptions (audited in M137)

- [`python/uv-audit`](../../templates/goons/python/uv-audit/goon.yml): `uv --preview audit --frozen --no-python-downloads --output-format json` reads the existing lock and contacts the advisory service as needed. `--frozen` and `--no-python-downloads` avoid dependency resolution and Python installation; neither is an offline advisory-data guarantee. The vulnerability service, redirects and transport endpoints are not under this catalog's fixed-address authority. A hostname list has *not* been verified end-to-end, so the shipped Goon explicitly keeps `host_unrestricted` rather than claiming a fabricated allowlist.
- [`security/trivy-audit`](../../templates/goons/security/trivy-audit/goon.yml): `trivy fs` can acquire or refresh vulnerability databases and related metadata through registry/CDN infrastructure. `--skip-version-check` and `--disable-telemetry` reduce unrelated checks but do not imply an offline database. Scanner DB mirrors, authentication, caches and redirects are deployment-dependent. A complete fixed destination set has *not* been proven, so `host_unrestricted` is retained intentionally. The `content_store` retention of the report is a separate artifact authority, not an egress grant.

Both are **outbound network exceptions with substantial reach**. Their
explicit capability can expose repository/source metadata to services contacted
by the native tools. Operators must evaluate this risk and run only with an
executor/environment that permits and can evidence that authority. A truly
offline deployment should supply a **separately designed and tested** native
advisory/DB mirror workflow; it must not remove egress or claim the existing
commands will still function without proving it.

All other currently shipped Atomic Goons retain deny-all network authority.
The security composite and `python/secure` Plan inherit the two exceptions
*only* through explicit ordered Goon composition, not global Plan escalation.
Typed OCI/Kubernetes operations obtain their **own** destination policies
from the existing typed execution owner and must not be confused with a
command Goon requesting host network access.

## Resolution, admission and evidence

1. Source: an Atomic Goon owns an explicit Step egress declaration; a Plan
   selects ordered Goons and forwards inputs without manufacturing egress.
2. The canonical `FleetPlanResolver` embeds the exact policy in the immutable
   resolved Steps; `preview_plan` exposes `egress_mode` without executing work.
3. Production admission and strict backend capability matching must account
   for the selected network mode and its host prerequisites **before work**.
   `hostname_allowlist` binds effective addresses once at the execution
   boundary and must not become unchecked live DNS access during effects.
4. Execution consumes the admitted policy. The Run/Step evidence and Enforcer
   receipts report declared/effective network policy; command exit status,
   stdout/stderr and Game Tape retain their existing owners. Network access
   does not authorize additional files, executables or automatic retries.

Changing an explicit mode changes the resolved Plan's execution authority;
it is never an implicit fallback after a failure. `host_unrestricted` does **not**
assert that every network destination is reachable; host/VPN/DNS failures
remain visible native tool failures, not grounds to silently widen permission.

## Template author review checklist

For each shipped Step with non-`none` egress, establish (1) which native
operation requires networking, (2) whether a verifiable fixed destination set
exists, (3) what happens on a fresh cache, (4) redirect/authentication/mirror
semantics, (5) host capability enforceability, (6) leak/exfiltration impact,
(7) preview/admission/Run evidence of the exact mode, and (8) a focused
regression preventing silent addition, removal or broadening. For
`host_unrestricted`, its package README must link to this guide, explicitly
name the mode, and explain why narrower alternatives are not yet proved.

See [Goon authoring](GOON_AUTHORING.md),
[template input transport](TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md),
[execution authority](EXECUTION_AUTHORITY_CLOSURE.md) and
[Enforcer receipts](ENFORCER_RECEIPTS.md).
