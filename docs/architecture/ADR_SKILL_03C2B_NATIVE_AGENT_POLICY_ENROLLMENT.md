# ADR — SKILL-3C2B: native enrollment is the single Agent limit source

Status: IMPLEMENTED, DEVELOPMENT ONLY. No runtime permission, no host qualification.

## Problem and authority boundary

SKILL-3C2A made the original Enforcer reject AG-02 effects outside an independently signed Run/Step ceiling. That does not establish **where the native issuer obtained the ceiling**. If a caller can copy an Agent's requested effect map into `step_envelopes.agent_effect_limits`, the resulting signed contract merely signs an unreviewed author demand.

## Decision

1. Add the **immutable `NativeAgentEffectCeilings`** policy value in the existing `fleet_native_run_contract_compiler` service. It is enrolled separately by the deployment owner and transported in `DeploymentRunContractPolicy`, the same frozen object already used by preplanning, native contract compilation and strict Fleet placement. No new mutable provider or signing key is introduced.
2. The existing `DeploymentRunContractPolicy.freeze/read` **reject any `agent_effect_limits` embedded in Step envelopes**, including after raw frozen bytes have been tampered with. The Agent-authored/request side remains `agent_requested_effects`, not an authority.
3. The original native compiler uses the original captured active Step IDs and demands **exactly one separately enrolled ceiling per active Agent Step**. A missing, extra, stale or non-Agent Step ceiling refuses. Command-only plans with an Agent policy refuse rather than silently ignore it.
4. Insert the separate limit into the one original candidate `AdmittedRunContract` after native observations and active-Step checks. Reuse the original `CausalRunJournal._independently_scoped_agent_effects` comparison instead of adding a second policy interpreter. The six existing native issuer claims and single original journal transaction remain unchanged.
5. The original Enforcer still correlates the AG-02 packet to the signed demand and validates the signed ceiling; the original signed prework journal explicitly records `effect_permission=NONE`.

## Boundary and threat model

`NativeAgentEffectCeilings` is **not** an independently attested production policy file or proof of administrative/OS source custody. It is a distinct immutable value that deployment initialization must obtain from an independently controlled source; untrusted Plans, skills, request envelopes, network responses and user-authored files must not create the deployment enrollment. No arbitrary Plan-selected issuer callback exists, but validating the upstream deployment provisioning remains work for SKILL-3C2C.

Direct manually constructed signed test contracts are not evidence that native enrollment occurred. Issuer signatures alone do not provide effect-time physical custody, a valid grant or permission for any model recipient, network socket, tool or repository mutation. Existing original Agent-Step Resolver and LACE `RESOURCE_INTENT` refusals stay in force. No trusted-local TL-1 inheritance.

## Testable contract

- Freeze policy from distinct enrollment value; mutate original dictionaries; emitted contract must remain unchanged.
- Inject `agent_effect_limits` inside a Step envelope; fail before native observation or admission.
- Omit independent policy, substitute other Step, add non-Agent Step, use malformed/noncanonical values or tamper with frozen bytes; refuse.
- Request tools/recipients/write outside narrowed ceiling; refuse using original Enforcer comparison.
- Command-only policy behavior remains compatible with existing native compilation.

Next: SKILL-3C2C proves external deployment policy provisioning, scoped Enforcer **grant** issuance, and effect-time LACE enforcement as separate, mandatory boundaries. Native Linux/WSL, APP PASS and release remain blocked.
