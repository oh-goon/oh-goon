import { useTranslation } from "react-i18next";

import type { FleetPlanTrustReceipt } from "@generated/models";
import { CLEAN_PASS_MASCOT } from "@/domain/presentation/hockeyTeam";

function digest(value: string | null | undefined): string {
  if (!value) return "—";
  return value.length > 18 ? `${value.slice(0, 12)}…${value.slice(-6)}` : value;
}

function yesNo(value: boolean, yes: string, no: string): string {
  return value ? yes : no;
}

export function PlanTrustReceipt({ receipt }: { receipt: FleetPlanTrustReceipt }) {
  const { t } = useTranslation();
  const cacheAttempts = (receipt.workspace?.attempts ?? []).filter((attempt) => (
    attempt.cache_input_sha256 || attempt.cache_output_sha256 || attempt.retained_reference_id
  ));
  const hostEnvironmentNames = receipt.runtime?.host_environment_names ?? [];
  const hostTools = receipt.runtime?.host_tools ?? [];
  const boundaries = receipt.boundaries ?? [];
  const releaseSteps = receipt.release.steps ?? [];
  const readinessSteps = receipt.release.readiness.steps ?? [];
  const artifacts = receipt.egress.artifacts ?? [];
  const outputs = receipt.egress.outputs ?? [];

  return <details className="history-details hockey-trust-receipt">
    <summary>{t("hockey.trustReceipt.title")}</summary>
    <p className="hockey-trust-receipt__intro">{t("hockey.trustReceipt.description")}</p>

    <div className="hockey-trust-receipt__identity">
      <div><span>{t("hockey.trustReceipt.planId")}</span><code>{receipt.plan_id}</code></div>
      <div><span>{t("hockey.trustReceipt.inputId")}</span><code>{receipt.input_id ?? "—"}</code></div>
      <div><span>{t("hockey.trustReceipt.repositoryId")}</span><code>{receipt.repository_id ?? "—"}</code></div>
    </div>

    <div className="hockey-trust-receipt__sections">
      <section className="hockey-trust-section">
        <header><span>{t("hockey.personas.enforcer.name")}</span><strong>{t("hockey.trustReceipt.admission.title")}</strong></header>
        {receipt.admission ? <>
          <dl className="technical-grid">
            <div><dt>{t("hockey.trustReceipt.admission.validationId")}</dt><dd><code>{receipt.admission.validation_id}</code></dd></div>
            <div><dt>{t("hockey.trustReceipt.admission.requirementsId")}</dt><dd><code>{receipt.admission.requirements_id}</code></dd></div>
            <div><dt>{t("hockey.trustReceipt.admission.requirementCount")}</dt><dd>{receipt.admission.requirement_count}</dd></div>
          </dl>
        </> : <p className="hockey-trust-note">{t("hockey.trustReceipt.admission.notRecorded")}</p>}
      </section>

      <section className="hockey-trust-section">
        <header><span>{t("hockey.personas.equipmentManager.name")}</span><strong>{t("hockey.trustReceipt.runtime.title")}</strong></header>
        {receipt.runtime ? <>
          <dl className="technical-grid">
            <div><dt>{t("hockey.trustReceipt.runtime.backend")}</dt><dd><code>{receipt.runtime.backend_id}</code></dd></div>
            <div><dt>{t("hockey.trustReceipt.runtime.toolchain")}</dt><dd><code title={receipt.runtime.host_toolchain_id}>{digest(receipt.runtime.host_toolchain_id)}</code></dd></div>
            <div><dt>{t("hockey.trustReceipt.runtime.configuration")}</dt><dd><code>{receipt.runtime.configuration_id}</code></dd></div>
            <div><dt>{t("hockey.trustReceipt.runtime.kernelBoundary")}</dt><dd>{receipt.runtime.kernel_boundary}</dd></div>
            <div><dt>{t("hockey.trustReceipt.runtime.remote")}</dt><dd>{yesNo(receipt.runtime.remote_execution, t("hockey.trustReceipt.yes"), t("hockey.trustReceipt.no"))}</dd></div>
            <div><dt>{t("hockey.trustReceipt.runtime.experimental")}</dt><dd>{yesNo(receipt.runtime.experimental, t("hockey.trustReceipt.yes"), t("hockey.trustReceipt.no"))}</dd></div>
            <div><dt>{t("hockey.trustReceipt.runtime.hostnameResolutions")}</dt><dd>{receipt.runtime.hostname_resolution_count}</dd></div>
          </dl>
          <div className="hockey-trust-list">
            <strong>{t("hockey.trustReceipt.runtime.hostEnvironmentNames")}</strong>
            <div className="hockey-chip-row">{hostEnvironmentNames.length ? hostEnvironmentNames.map((name) => <code key={name}>{name}</code>) : <span>—</span>}</div>
          </div>
          <div className="hockey-trust-list">
            <strong>{t("hockey.trustReceipt.runtime.hostTools")}</strong>
            {hostTools.length ? <ul>{hostTools.map((tool) => <li key={tool.authority_id}>
              <strong><code>{tool.name}</code></strong>
              <span>{tool.origin} · {t("hockey.trustReceipt.runtime.bytes", { count: tool.byte_count })}</span>
              <code title={tool.sha256}>{digest(tool.sha256)}</code>
              <code title={tool.authority_id}>{digest(tool.authority_id)}</code>
            </li>)}</ul> : <span>—</span>}
          </div>
        </> : <p className="hockey-trust-note">{t("hockey.trustReceipt.runtime.notRecorded")}</p>}
      </section>

      <section className="hockey-trust-section">
        <header><span>{t("hockey.personas.warmUp.name")}</span><strong>{t("hockey.trustReceipt.cache.title")}</strong></header>
        {receipt.workspace ? <>
          <dl className="technical-grid">
            <div><dt>{t("hockey.trustReceipt.cache.baseWorkspace")}</dt><dd><code title={receipt.workspace.base_workspace_sha256}>{digest(receipt.workspace.base_workspace_sha256)}</code></dd></div>
            <div><dt>{t("hockey.trustReceipt.cache.runtimeRoot")}</dt><dd><code title={receipt.workspace.runtime_root_sha256}>{digest(receipt.workspace.runtime_root_sha256)}</code></dd></div>
            <div><dt>{t("hockey.trustReceipt.cache.managedInputs")}</dt><dd>{receipt.workspace.managed_input_count}</dd></div>
            <div><dt>{t("hockey.trustReceipt.cache.contentInputs")}</dt><dd>{receipt.workspace.content_input_count}</dd></div>
          </dl>
          {cacheAttempts.length ? <ul className="hockey-cache-lineage">{cacheAttempts.map((attempt) => <li key={`${attempt.step_id}:${attempt.attempt}`}>
            <strong>{attempt.step_id} · {t("hockey.shiftReport.attempt", { attempt: attempt.attempt })}</strong>
            <span>{t("hockey.trustReceipt.cache.input")} <code title={attempt.cache_input_sha256 ?? undefined}>{digest(attempt.cache_input_sha256)}</code></span>
            <span>{t("hockey.trustReceipt.cache.output")} <code title={attempt.cache_output_sha256 ?? undefined}>{digest(attempt.cache_output_sha256)}</code></span>
            <span>{t("hockey.trustReceipt.cache.retained")} <code>{attempt.retained_reference_id ?? "—"}</code></span>
            {attempt.retained_owner_repository_id ? <span>{t("hockey.trustReceipt.cache.owner")} <code>{attempt.retained_owner_repository_id}</code></span> : null}
            {attempt.retained_sha256 ? <span>{t("hockey.trustReceipt.cache.retainedDigest")} <code title={attempt.retained_sha256}>{digest(attempt.retained_sha256)}</code></span> : null}
          </li>)}</ul> : <p className="hockey-trust-note">{t("hockey.trustReceipt.cache.noAttemptCache")}</p>}
        </> : <p className="hockey-trust-note">{t("hockey.trustReceipt.cache.notRecorded")}</p>}
      </section>

      <section className="hockey-trust-section">
        <header><span>{t("hockey.personas.linesman.name")}</span><strong>{t("hockey.trustReceipt.boundaries.title")}</strong></header>
        {boundaries.length ? <ul className="hockey-boundary-sheet">{boundaries.map((boundary) => <li key={`${boundary.step_id}:${boundary.attempt}`}>
          <strong>{boundary.step_id} · {t("hockey.shiftReport.attempt", { attempt: boundary.attempt })}</strong>
          <span>{t("hockey.trustReceipt.boundaries.egress")} <code>{boundary.egress_mode}</code> · <code>{boundary.egress_policy_id}</code></span>
          <span>{t("hockey.trustReceipt.boundaries.resources")} <code>{boundary.resource_policy_id ?? "—"}</code></span>
          <span>{t("hockey.trustReceipt.boundaries.repositoryState")} <code title={boundary.repository_state_before}>{digest(boundary.repository_state_before)}</code> → <code title={boundary.repository_state_after}>{digest(boundary.repository_state_after)}</code></span>
        </li>)}</ul> : <p className="hockey-trust-note">{t("hockey.trustReceipt.boundaries.notRecorded")}</p>}
      </section>

      <section className="hockey-trust-section hockey-trust-section--wide">
        <header><span>{t("hockey.personas.goalie.name")}</span><strong>{t("hockey.trustReceipt.egress.title")}</strong></header>
        <div className="hockey-goalie-score">
          <div><span>{t("hockey.trustReceipt.release.state")}</span><strong>{t(`hockey.trustReceipt.release.states.${receipt.release.state}`)}</strong></div>
          <div><span>{t("hockey.trustReceipt.release.released")}</span><strong>{receipt.release.released}</strong></div>
          <div><span>{t("hockey.trustReceipt.release.blocked")}</span><strong>{receipt.release.blocked}</strong></div>
          <div><span>{t("hockey.trustReceipt.release.gaps")}</span><strong>{receipt.release.evidence_gaps}</strong></div>
          <div><span>{t("hockey.trustReceipt.release.externalEffects")}</span><strong>{receipt.release.external_effects}</strong></div>
        </div>
        {releaseSteps.length ? <ul className="hockey-release-ledger">{releaseSteps.map((step) => <li key={step.step_id}>
          <div className="hockey-release-ledger__head"><code>{step.step_id}</code><span>{step.step_kind}</span><strong>{t(`hockey.trustReceipt.release.gateStates.${step.gate_state}`)}</strong><small>{step.execution_status ?? "—"}</small></div>
          {step.release?.kind === "OCI_PUBLISH" ? <dl>
            <div><dt>{t("hockey.trustReceipt.release.releaseId")}</dt><dd><code>{step.release.release_id}</code></dd></div>
            <div><dt>{t("hockey.trustReceipt.release.target")}</dt><dd><code>{step.release.target}</code></dd></div>
            <div><dt>{t("hockey.trustReceipt.release.digest")}</dt><dd><code>{step.release.manifest_digest}</code></dd></div>
          </dl> : step.release?.kind === "KUBERNETES_SET_IMAGE" ? <dl>
            <div><dt>{t("hockey.trustReceipt.release.releaseId")}</dt><dd><code>{step.release.release_id}</code></dd></div>
            <div><dt>{t("hockey.trustReceipt.release.target")}</dt><dd><code>{step.release.target}</code></dd></div>
            <div><dt>{t("hockey.trustReceipt.release.outcome")}</dt><dd>{step.release.outcome}</dd></div>
            <div><dt>{t("hockey.trustReceipt.release.image")}</dt><dd><code>{step.release.desired_image}</code></dd></div>
            <div><dt>{t("hockey.trustReceipt.release.resourceVersion")}</dt><dd><code>{step.release.before_resource_version}</code> → <code>{step.release.after_resource_version}</code></dd></div>
            <div><dt>{t("hockey.trustReceipt.release.generation")}</dt><dd>{step.release.before_generation} → {step.release.after_generation}</dd></div>
          </dl> : <p>{t("hockey.trustReceipt.release.noCanonicalEffect")}</p>}
        </li>)}</ul> : <p className="hockey-trust-note">{t("hockey.trustReceipt.release.none")}</p>}
        {receipt.release.external_effects > 0 && receipt.final.state === "failed" ? <p className="hockey-release-warning">{t("hockey.trustReceipt.release.failedAfterRelease")}</p> : null}
        <div className="hockey-readiness-score">
          <div><span>{t("hockey.trustReceipt.readiness.state")}</span><strong>{t(`hockey.trustReceipt.readiness.states.${receipt.release.readiness.state}`)}</strong></div>
          <div><span>{t("hockey.trustReceipt.readiness.requested")}</span><strong>{receipt.release.readiness.requested}</strong></div>
          <div><span>{t("hockey.trustReceipt.readiness.ready")}</span><strong>{receipt.release.readiness.ready}</strong></div>
          <div><span>{t("hockey.trustReceipt.readiness.failed")}</span><strong>{receipt.release.readiness.failed}</strong></div>
          <div><span>{t("hockey.trustReceipt.readiness.blocked")}</span><strong>{receipt.release.readiness.blocked}</strong></div>
          <div><span>{t("hockey.trustReceipt.readiness.gaps")}</span><strong>{receipt.release.readiness.evidence_gaps}</strong></div>
        </div>
        {readinessSteps.length ? <ul className="hockey-readiness-ledger">{readinessSteps.map((step) => <li key={step.step_id}>
          <div className="hockey-readiness-ledger__head"><code>{step.step_id}</code><strong>{t(`hockey.trustReceipt.readiness.stepStates.${step.state}`)}</strong><small>{step.execution_status ?? "—"}</small></div>
          {step.verification ? <dl>
            <div><dt>{t("hockey.trustReceipt.readiness.verificationId")}</dt><dd><code>{step.verification.verification_id}</code></dd></div>
            <div><dt>{t("hockey.trustReceipt.readiness.outcome")}</dt><dd>{step.verification.outcome}</dd></div>
            <div><dt>{t("hockey.trustReceipt.readiness.target")}</dt><dd><code>{step.verification.target}</code></dd></div>
            <div><dt>{t("hockey.trustReceipt.readiness.generation")}</dt><dd>{step.verification.expected_generation} → {step.verification.final_generation} · {t("hockey.trustReceipt.readiness.observed", { generation: step.verification.observed_generation ?? "—" })}</dd></div>
            <div><dt>{t("hockey.trustReceipt.readiness.image")}</dt><dd><code>{step.verification.expected_image}</code></dd></div>
            <div><dt>{t("hockey.trustReceipt.readiness.finalImage")}</dt><dd><code>{step.verification.final_image}</code></dd></div>
            <div><dt>{t("hockey.trustReceipt.readiness.replicas")}</dt><dd>{t("hockey.trustReceipt.readiness.replicaCounts", { desired: step.verification.desired_replicas, current: step.verification.replicas, updated: step.verification.updated_replicas, available: step.verification.available_replicas, unavailable: step.verification.unavailable_replicas })}</dd></div>
            <div><dt>{t("hockey.trustReceipt.readiness.observations")}</dt><dd>{step.verification.observations}</dd></div>
            <div><dt>{t("hockey.trustReceipt.readiness.resourceVersion")}</dt><dd><code>{step.verification.final_resource_version}</code></dd></div>
            <div><dt>{t("hockey.trustReceipt.readiness.progressing")}</dt><dd>{step.verification.progressing_status ?? "—"}{step.verification.progressing_reason ? ` · ${step.verification.progressing_reason}` : ""}</dd></div>
            <div><dt>{t("hockey.trustReceipt.readiness.window")}</dt><dd>{t("hockey.trustReceipt.readiness.windowValues", { timeout: step.verification.timeout_seconds, poll: step.verification.poll_interval_seconds, request: step.verification.request_timeout_seconds })}</dd></div>
          </dl> : <p>{step.state === "blocked" ? t("hockey.trustReceipt.readiness.notRun") : t("hockey.trustReceipt.readiness.noCanonicalVerification")}</p>}
        </li>)}</ul> : <p className="hockey-trust-note">{t("hockey.trustReceipt.readiness.notRequested")}</p>}
        <p className="hockey-trust-note">{t("hockey.trustReceipt.readiness.rule")}</p>
        <div className="hockey-egress-manifest">
          <div><strong>{t("hockey.shiftReport.artifacts")}</strong>{artifacts.length ? <ul>{artifacts.map((artifact) => <li key={`${artifact.step_id}:${artifact.path}`}><code>{artifact.step_id}</code><code>{artifact.path}</code><span>{artifact.present ? `${artifact.byte_count ?? 0} B` : t("hockey.shiftReport.missing")}</span><code title={artifact.sha256 ?? undefined}>{digest(artifact.sha256)}</code></li>)}</ul> : <p>{t("hockey.trustReceipt.egress.noArtifacts")}</p>}</div>
          <div><strong>{t("hockey.shiftReport.outputs")}</strong>{outputs.length ? <ul>{outputs.map((output) => <li key={`${output.step_id}:${output.name}`}><code>{output.step_id}</code><code>{output.name}</code><span>{output.byte_count} B</span><code title={output.sha256}>{digest(output.sha256)}</code></li>)}</ul> : <p>{t("hockey.trustReceipt.egress.noOutputs")}</p>}</div>
        </div>
        <p className="hockey-trust-note">{t("hockey.trustReceipt.egress.rule")}</p>
      </section>

      <section className="hockey-trust-section hockey-trust-section--wide">
        <header><span>{t("hockey.personas.zamboni.name")}</span><strong>{t("hockey.trustReceipt.teardown.title")}</strong></header>
        <div className="hockey-final-whistle">
          <div><span>{t("hockey.trustReceipt.finalWhistle.run")}</span><strong>{t(`hockey.trustReceipt.finalWhistle.states.${receipt.final.state}`)}</strong></div>
          <div><span>{t("hockey.trustReceipt.finalWhistle.steps")}</span><strong>{receipt.final.completed_steps} / {receipt.final.failed_steps} / {receipt.final.skipped_steps}</strong><small>{t("hockey.trustReceipt.finalWhistle.stepLegend")}</small></div>
          <div><span>{t("hockey.trustReceipt.finalWhistle.teardown")}</span><strong>{receipt.final.teardown_verified} / {receipt.final.teardown_failed} / {receipt.final.teardown_missing}</strong><small>{t("hockey.trustReceipt.finalWhistle.teardownLegend")}</small></div>
        </div>
        <ul className="hockey-teardown-sheet">{receipt.teardown.steps.map((step) => <li key={step.step_id}>
          <code>{step.step_id}</code>
          <strong>{t(`hockey.trustReceipt.teardown.states.${step.status}`)}</strong>
          <span>{step.attempt_roots_observed === null ? "—" : t("hockey.trustReceipt.teardown.attemptRoots", { count: step.attempt_roots_observed })}</span>
          {step.reason_code ? <code>{step.reason_code}</code> : null}
        </li>)}</ul>
        <small className="hockey-trust-note">{t("hockey.trustReceipt.teardown.rule")}</small>
        {receipt.final.state === "cleanPass" ? <div className="hockey-clean-pass" role="status"><pre aria-hidden="true">{CLEAN_PASS_MASCOT}</pre><p>{t("hockey.trustReceipt.finalWhistle.cleanPassRule")}</p></div> : null}
      </section>
    </div>
  </details>;
}
