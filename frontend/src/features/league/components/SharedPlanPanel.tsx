import type { SharedPlanInputs } from "@/domain/api/ApiClient";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import { advanceIdentityIncarnation, sameIdentityIncarnation, type IdentityIncarnation } from "@/domain/concurrency/identityIncarnation";

import { useApiClient, useReportMutationFeedback } from "@/app/ApiClientProvider";
import { appDiagnosticStore } from "@/domain/diagnostics/appDiagnosticStore";

import type { AgentCampaignResult, FleetRunState, LeagueLineupDocument, LeagueMap, RunAgentSelection, SharedPlanPreview } from "@generated/models";
import { newFleetRunId } from "@/domain/identity/commandIdentity";
import { RepositorySelectionPanel } from "@/features/league/components/RepositorySelectionPanel";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";
import { useLeagueLineups } from "@/features/league/hooks/useLeagueLineups";
import { useLeagueLineupInspection } from "@/features/league/hooks/useLeagueLineupInspection";
import { useSharedPlans } from "@/features/league/hooks/useSharedPlans";
import { useSharedPlanDocument } from "@/features/league/hooks/useSharedPlanDocument";
import { PlanInputDiagnostic } from "@/components/common/PlanInputDiagnostic";
import { useSharedPlanInputInspection } from "@/features/league/hooks/useSharedPlanInputInspection";
import { useSharedPlanLockPreview } from "@/features/league/hooks/useSharedPlanLockPreview";
import { useSharedPlanPreview } from "@/features/league/hooks/useSharedPlanPreview";
import { editorWriteAuthority } from "@/features/league/writeAuthority";
import { EMPTY_SHARED_PLAN_AGENT, SharedPlanAgentFields, sharedPlanAgentSelection, sharedPlanRepairSelection } from "@/features/league/components/SharedPlanAgentFields";
import {
  ExecutionPlanInputFields,
  executionPlanInputs,
  initialExecutionPlanInputValues,
  requiredExecutionPlanInputsComplete,
  type ExecutionPlanInputValues,
} from "@/features/plans/PlansFeature";

interface Props {
  league: LeagueMap | null;
  actionsDisabled?: boolean;
  onRun: (planKey: string, repositoryIds: string[], expectedPlanId: string, expectedPlanRevision: string, expectedExecutionDigest: string, repositoryInstanceIds: Record<string, string>, inputs: SharedPlanInputs, agent?: RunAgentSelection | null) => Promise<WriteCommandResult<FleetRunState>>;
  onOpenCampaign?: (campaignId: string) => void;
  onAcceptedRun?: (fleetRunId: string) => void;
  initialRepositoryIds?: readonly string[];
  initialRepositoryInstanceIds?: Readonly<Record<string, string>>;
  onOpenPlans?: () => void;
}

type SubmissionState =
  | { state: "idle" }
  | { state: "submitting"; owner: IdentityIncarnation }
  | { state: "accepted"; owner: IdentityIncarnation }
  | { state: "rejected"; owner: IdentityIncarnation; title: string; message: string };

export function SharedPlanPanel({ league, actionsDisabled = false, onRun, onAcceptedRun, initialRepositoryIds = [], initialRepositoryInstanceIds, onOpenPlans, onOpenCampaign }: Props) {
  const { t } = useTranslation();
  const client = useApiClient();
  const reportLineupMutation = useReportMutationFeedback("league-lineups");
  const reportRepairMutation = useReportMutationFeedback("fleet-run");
  const plans = useSharedPlans();
  const [savedSelectionsOpen, setSavedSelectionsOpen] = useState(false);
  const lineups = useLeagueLineups(savedSelectionsOpen);
  const catalog = plans.resource.data?.plans ?? [];
  const plansLoading = plans.resource.status === "read-loading" || plans.resource.status === "read-refreshing";
  const lineupCatalog = lineups.resource.data?.lineups ?? [];
  const repositories = useMemo(() => league?.teams ?? [], [league]);
  const availableRepositoryIds = useMemo(
    () => new Set(repositories.map((entry) => entry.repository.repository_id)),
    [repositories],
  );
  const [planKey, setPlanKey] = useState("");
  const [lineupKey, setLineupKey] = useState("");
  const lineupInspection = useLeagueLineupInspection(lineupKey);
  const [selectedIds, setSelectedIds] = useState<string[]>(() => [...initialRepositoryIds]);
  const [inputValues, setInputValues] = useState<ExecutionPlanInputValues>({});
  const [agentDraft, setAgentDraft] = useState(EMPTY_SHARED_PLAN_AGENT);
  const [repairAuthorization, setRepairAuthorization] = useState<IdentityIncarnation | null>(null);
  const [repairHandoff, setRepairHandoff] = useState<{ id: string; result: AgentCampaignResult | null; uncertain: boolean } | null>(null);
  const agentSelection = useMemo(() => sharedPlanAgentSelection(agentDraft), [agentDraft]);
  const repairSelection = useMemo(() => sharedPlanRepairSelection(agentDraft), [agentDraft]);
  const agentReady = agentSelection === null || Boolean(
    agentSelection.acceptance && agentSelection.repository_root && agentSelection.skill_root
    && (!agentSelection.implementation || agentSelection.profile) && selectedIds.length === 1,
  );
  const [lineupDraftKey, setLineupDraftKey] = useState("");
  const [lineupName, setLineupName] = useState("");
  const [lineupRevision, setLineupRevision] = useState<string | null>(null);
  const [submission, setSubmission] = useState<SubmissionState>({ state: "idle" });
  const [blockedAttemptRevision, setBlockedAttemptRevision] = useState(0);
  const [runAttemptError, setRunAttemptError] = useState<{ owner: IdentityIncarnation; title: string; message: string } | null>(null);
  const [message, setMessage] = useState<{ owner: IdentityIncarnation; text: string } | null>(null);
  const [lineupDetached, setLineupDetached] = useState(false);
  const observedLineupRevision = lineupInspection.resource.data?.lineup_key === lineupKey ? lineupInspection.resource.data.revision : null;
  const lineupEditorStale = Boolean(lineupKey && lineupRevision !== null && observedLineupRevision !== null && observedLineupRevision !== lineupRevision);
  const lineupTargetKey = lineupDraftKey.trim();
  const canSaveLineup = editorWriteAuthority({
    catalogFresh: lineups.resource.status === "read-fresh",
    targetKey: lineupTargetKey,
    ownedKey: lineupKey,
    targetReadFresh: lineupInspection.resource.status === "read-fresh",
    existingKeys: lineupCatalog.map((lineup) => lineup.lineup_key),
  })
    && !lineupEditorStale;
  const initializedPlanRevisionRef = useRef<string | null>(null);
  const appliedLineupInspectionRef = useRef<string | null>(null);
  const runOwnerRef = useRef<IdentityIncarnation>({ identity: "", generation: 0 });
  const lineupOwnerRef = useRef<IdentityIncarnation>({ identity: "", generation: 0 });
  lineupOwnerRef.current = advanceIdentityIncarnation(lineupOwnerRef.current, canonicalIdentity({
    lineupKey, lineupDraftKey, lineupName, lineupRevision, repositoryIds: [...selectedIds].sort(),
  }));

  useEffect(() => {
    if (planKey && !catalog.some((plan) => plan.plan_key === planKey)) {
      setPlanKey("");
    }
  }, [catalog, planKey]);

  const planDocumentRead = useSharedPlanDocument(planKey);
  const selectedPlanDocument = planDocumentRead.resource.data;
  const planLoadError = planDocumentRead.resource.failure?.message ?? null;

  useEffect(() => {
    if (!planKey) {
      initializedPlanRevisionRef.current = null;
      setInputValues({});
      setRunAttemptError(null);
      setSubmission({ state: "idle" });
      return;
    }
    if (!selectedPlanDocument) return;
    const documentIdentity = canonicalIdentity({ planKey: selectedPlanDocument.plan_key, sha256: selectedPlanDocument.sha256 });
    if (initializedPlanRevisionRef.current === documentIdentity) return;
    initializedPlanRevisionRef.current = documentIdentity;
    setInputValues(initialExecutionPlanInputValues(selectedPlanDocument.inputs ?? []));
    setRunAttemptError(null);
    setSubmission({ state: "idle" });
  }, [planKey, selectedPlanDocument]);

  useEffect(() => {
    if (league == null) return;
    setSelectedIds((current) => current.filter((id) => availableRepositoryIds.has(id)));
  }, [availableRepositoryIds, league]);

  const inputDefinitions = (selectedPlanDocument?.inputs ?? []).filter((definition) => !agentDraft.enabled || definition.name !== "acceptance");
  const requiredInputsComplete = requiredExecutionPlanInputsComplete(inputDefinitions, inputValues);
  const submittedInputs = useMemo(() => {
    const values = executionPlanInputs(inputValues);
    if (agentDraft.enabled) delete values.acceptance;
    return values;
  }, [inputValues, agentDraft.enabled]);
  const inputInspectionRead = useSharedPlanInputInspection(
    planKey, selectedPlanDocument?.sha256 ?? "", submittedInputs, Boolean(planKey && selectedPlanDocument && agentReady), agentSelection, selectedIds,
  );
  const inputInspection = inputInspectionRead.resource.status === "read-fresh" ? inputInspectionRead.resource.data : null;
  const lockPreviewRead = useSharedPlanLockPreview(
    planKey,
    selectedPlanDocument?.sha256 ?? "",
    submittedInputs,
    Boolean(planKey && selectedPlanDocument && requiredInputsComplete && agentReady),
    undefined, agentSelection, selectedIds,
  );
  const selectedLeagueRepositoryInstanceIds = useMemo(() => {
    const selected = new Set(selectedIds);
    return Object.fromEntries(
      repositories
        .filter((entry) => selected.has(entry.repository.repository_id))
        .map((entry) => [entry.repository.repository_id, entry.repository_instance_id]),
    );
  }, [repositories, selectedIds]);
  const previewExpectedRepositoryInstanceIds = useMemo(() => {
    if (initialRepositoryInstanceIds !== undefined) {
      const selectedPopulation = [...selectedIds].sort();
      const materializedPopulation = Object.keys(initialRepositoryInstanceIds).sort();
      if (canonicalIdentity(selectedPopulation) === canonicalIdentity(materializedPopulation)) {
        return initialRepositoryInstanceIds;
      }
    }
    return selectedLeagueRepositoryInstanceIds;
  }, [initialRepositoryInstanceIds, selectedIds, selectedLeagueRepositoryInstanceIds]);
  const previewRead = useSharedPlanPreview({
    planKey,
    planRevision: selectedPlanDocument?.sha256 ?? "",
    repositoryIds: selectedIds,
    expectedRepositoryInstanceIds: previewExpectedRepositoryInstanceIds,
    inputs: submittedInputs,
    agent: agentSelection,
    enabled: Boolean(planKey && selectedPlanDocument && selectedIds.length > 0 && requiredInputsComplete && agentReady),
  });
  const lockPreview = lockPreviewRead.resource.data;
  const lockPreviewing = lockPreviewRead.resource.status === "read-loading" || lockPreviewRead.resource.status === "read-refreshing";
  const lockPreviewError = lockPreviewRead.resource.failure?.message ?? null;
  const preview = previewRead.resource.data;
  const previewing = previewRead.resource.status === "read-loading" || previewRead.resource.status === "read-refreshing";
  const previewError = previewRead.resource.failure?.message ?? null;

  const previewRepositoryInstanceIds = preview === null
    ? null
    : Object.fromEntries(
        preview.teams.map((team) => [team.repository.repository_id, team.repository_instance_id]),
      );
  runOwnerRef.current = advanceIdentityIncarnation(runOwnerRef.current, canonicalIdentity({
    planKey,
    planRevision: preview?.sha256 ?? selectedPlanDocument?.sha256 ?? null,
    repositoryIds: [...selectedIds].sort(),
    repositoryInstanceIds: previewRepositoryInstanceIds,
    planId: preview?.plan_id ?? null,
    executionDigest: preview?.execution_digest ?? null,
    reviewedExecutionDigest: lockPreview?.execution_digest ?? null,
    inputs: submittedInputs,
    agent: agentSelection, repair: agentDraft.repair ? agentDraft : null,
  }));
  const currentRunOwner = runOwnerRef.current;
  const currentSubmission = submission.state === "idle" || sameIdentityIncarnation(submission.owner, currentRunOwner)
    ? submission
    : { state: "idle" as const };
  const currentRunAttemptError = runAttemptError && sameIdentityIncarnation(runAttemptError.owner, currentRunOwner) ? runAttemptError : null;
  const submitting = currentSubmission.state === "submitting";
  const submissionPending = submission.state === "submitting";

  const selected = useMemo(() => new Set(selectedIds), [selectedIds]);
  const selectedPaths = useMemo(
    () => repositories.filter((entry) => selected.has(entry.repository.repository_id)).map((entry) => entry.repository.path),
    [repositories, selected],
  );
  const selectedPlan = catalog.find((plan) => plan.plan_key === planKey) ?? null;
  const previewFresh = previewRead.resource.status === "read-fresh";
  const lockPreviewFresh = lockPreviewRead.resource.status === "read-fresh";
  const lockMatchesPreview = preview !== null && lockPreview !== null && lockPreview.plan_id === preview.plan_id && lockPreview.sha256 === preview.sha256 && lockPreview.execution_digest === preview.execution_digest;
  const canAttemptRun = agentReady && requiredInputsComplete && previewFresh && lockPreviewFresh && lockMatchesPreview && preview !== null && preview.ready_count + preview.blocked_count === selectedIds.length;
  const repairAuthorized = repairAuthorization !== null && sameIdentityIncarnation(repairAuthorization, currentRunOwner);
  const repairReady = repairSelection !== null && repairSelection.write_roots.length > 0
    && [repairSelection.review_passes, repairSelection.timeout_seconds, repairSelection.step_timeout_seconds].every(Number.isFinite);

  const chooseIds = (ids: string[]) => {
    setSelectedIds(ids);
    setRunAttemptError(null);
    setSubmission({ state: "idle" });
  };

  const submitRun = async () => {
    if (!preview || !previewFresh || !canAttemptRun || submissionPending || actionsDisabled) return;
    if (repairSelection !== null && (!agentSelection || !repairAuthorized || !repairReady)) return;
    if (preview.blocked_count > 0) {
      setRunAttemptError({
        owner: currentRunOwner,
        title: t("sharedPlans.verification.runBlockedTitle"),
        message: t("sharedPlans.verification.runBlockedMessage", { blocked: preview.blocked_count, count: selectedIds.length }),
      });
      setBlockedAttemptRevision((value) => value + 1);
      return;
    }
    setRunAttemptError(null);
    const commandOwner = runOwnerRef.current;
    setSubmission({ state: "submitting", owner: commandOwner });
    let submittedCampaignId: string | null = null;
    try {
      const repositoryInstanceIds = previewRepositoryInstanceIds!;
      if (repairSelection !== null) {
        if (!agentSelection || !repairAuthorized || !repairReady) return;
        const campaignId = newFleetRunId();
        submittedCampaignId = campaignId;
        setRepairAuthorization(null);
        setRepairHandoff({ id: campaignId, result: null, uncertain: false });
        const result = await client.repairSharedPlan({
          fleet_run_id: campaignId, plan_key: planKey, expected_plan_id: preview.plan_id,
          expected_plan_revision: preview.sha256, expected_execution_digest: preview.execution_digest,
          repository_ids: selectedIds, repository_instance_ids: repositoryInstanceIds,
          inputs: submittedInputs, agent: agentSelection, repair: repairSelection, authorize_repair: true,
        });
        if (result.outcome !== "saved") reportRepairMutation(result);
        const retained = result.outcome === "saved" && result.value.campaign_id === campaignId ? result.value : null;
        setRepairHandoff({ id: campaignId, result: retained, uncertain: retained === null });
        if (sameIdentityIncarnation(runOwnerRef.current, commandOwner)) setSubmission({ state: "idle" });
        return;
      }
      const result = await onRun(planKey, selectedIds, preview.plan_id, preview.sha256, preview.execution_digest, repositoryInstanceIds, submittedInputs, agentSelection);
      if (!sameIdentityIncarnation(runOwnerRef.current, commandOwner)) return;
      if (result.outcome === "saved") {
        setSubmission({ state: "accepted", owner: commandOwner });
        onAcceptedRun?.(result.value.fleet_run_id);
      } else if (result.outcome === "stale") {
        setSubmission({
          state: "rejected",
          owner: commandOwner,
          title: t("sharedPlans.verification.submitFailedTitle"),
          message: t("sharedPlans.verification.submitRejectedStale", { diagnosticId: result.diagnosticId }),
        });
      } else {
        setSubmission({
          state: "rejected",
          owner: commandOwner,
          title: t("sharedPlans.verification.submitFailedTitle"),
          message: t("sharedPlans.verification.submitRejectedWrite", { diagnosticId: result.diagnosticId }),
        });
      }
    } catch (error) {
      if (submittedCampaignId !== null) {
        setRepairHandoff({ id: submittedCampaignId, result: null, uncertain: true });
      }
      if (!sameIdentityIncarnation(runOwnerRef.current, commandOwner)) return;
      setSubmission({
        state: "rejected",
        owner: commandOwner,
        title: t("sharedPlans.verification.submitFailedTitle"),
        message: t("sharedPlans.verification.submitFailedMessage", { message: error instanceof Error ? error.message : String(error) }),
      });
    }
  };

  const loadLineup = (key: string) => {
    appliedLineupInspectionRef.current = null;
    setLineupDetached(false);
    setLineupKey(key);
    if (!key) {
      setLineupRevision(null);
      setLineupDraftKey("");
      setLineupName("");
    }
  };

  useEffect(() => {
    if (!lineupKey) return;
    const inspection = lineupInspection.resource.data;
    if (!inspection || inspection.lineup_key !== lineupKey) return;
    if (appliedLineupInspectionRef.current !== null) return;
    const inspectionIdentity = canonicalIdentity({ lineupKey: inspection.lineup_key, revision: inspection.revision });
    appliedLineupInspectionRef.current = inspectionIdentity;
    setLineupDraftKey(inspection.lineup_key);
    setLineupName(inspection.name);
    setLineupRevision(inspection.revision);
    const visibleIds = new Set(repositories.map((entry) => entry.repository.repository_id));
    const readyIds = inspection.teams
      .filter((entry) => "repository" in entry)
      .map((entry) => entry.repository.repository_id)
      .filter((id) => visibleIds.has(id));
    setSelectedIds(readyIds);
    setRunAttemptError(null);
    setSubmission({ state: "idle" });
    const unavailable = inspection.unavailable_count + inspection.ready_count - readyIds.length;
    const loadedOwner = canonicalIdentity({
      lineupKey: inspection.lineup_key,
      lineupDraftKey: inspection.lineup_key,
      lineupName: inspection.name,
      lineupRevision: inspection.revision,
      repositoryIds: [...readyIds].sort(),
    });
    setMessage({
      owner: advanceIdentityIncarnation(lineupOwnerRef.current, loadedOwner),
      text: unavailable > 0
        ? t("sharedPlans.lineupUnavailable", { count: unavailable })
        : t("sharedPlans.lineupLoaded", { name: inspection.name }),
    });
  }, [lineupInspection.resource.data, lineupKey, repositories, t]);

  useEffect(() => {
    if (lineupEditorStale) setMessage(null);
  }, [lineupEditorStale]);

  useEffect(() => {
    if (lineupKey && lineups.resource.status === "read-fresh" && !lineupCatalog.some((lineup) => lineup.lineup_key === lineupKey)) {
      setLineupKey("");
      setLineupRevision(null);
      appliedLineupInspectionRef.current = null;
      setLineupDetached(true);
      setMessage(null);
    }
  }, [lineupCatalog, lineupKey, lineups.resource.status]);

  useEffect(() => {
    if (lineupKey && lineupInspection.resource.status === "read-error") {
      setMessage({ owner: lineupOwnerRef.current, text: t("sharedPlans.lineupLoadFailed") });
    }
  }, [lineupInspection.resource.status, lineupKey, t]);

  const loadLatestServerLineup = () => {
    const inspection = lineupInspection.resource.data;
    if (!inspection || inspection.lineup_key !== lineupKey) return;
    const visibleIds = new Set(repositories.map((entry) => entry.repository.repository_id));
    const readyIds = inspection.teams
      .filter((entry) => "repository" in entry)
      .map((entry) => entry.repository.repository_id)
      .filter((id) => visibleIds.has(id));
    appliedLineupInspectionRef.current = canonicalIdentity({ lineupKey: inspection.lineup_key, revision: inspection.revision });
    setLineupDraftKey(inspection.lineup_key);
    setLineupName(inspection.name);
    setLineupRevision(inspection.revision);
    setLineupDetached(false);
    setSelectedIds(readyIds);
    setRunAttemptError(null);
    setSubmission({ state: "idle" });
    setMessage(null);
  };

  const saveLineup = async () => {
    if (!lineupDraftKey.trim() || !lineupName.trim() || selectedPaths.length === 0 || !canSaveLineup) return;
    const key = lineupDraftKey.trim();
    const expectedRevision = key === lineupKey ? lineupRevision : null;
    const commandOwner = lineupOwnerRef.current;
    const result = validateSavedLineupIdentity(
      await client.saveLeagueLineup(key, lineupName.trim(), selectedPaths, expectedRevision),
      key,
    );
    reportLineupMutation(result);
    if (result.outcome === "saved" && sameIdentityIncarnation(lineupOwnerRef.current, commandOwner)) {
      setLineupKey(result.value.lineup_key);
      setLineupRevision(result.value.revision);
      setLineupDetached(false);
      appliedLineupInspectionRef.current = canonicalIdentity({ lineupKey: result.value.lineup_key, revision: result.value.revision });
      const committedOwner = canonicalIdentity({
        lineupKey: result.value.lineup_key,
        lineupDraftKey: result.value.lineup_key,
        lineupName,
        lineupRevision: result.value.revision,
        repositoryIds: [...selectedIds].sort(),
      });
      setMessage({ owner: advanceIdentityIncarnation(lineupOwnerRef.current, committedOwner), text: t("sharedPlans.lineupSaved", { name: result.value.lineup_key }) });
    }
  };

  return <section className="panel shared-plan-panel shared-plan-workspace" aria-busy={previewing || lockPreviewing || submissionPending}>
    {plansLoading && plans.resource.data === null ? <p className="muted loading-state" role="status">{t("sharedPlans.loading")}</p> : null}
    {!plansLoading && catalog.length === 0 ? <div className="run-setup-edge-state" role="status"><strong>{t("sharedPlans.execution.noPlansTitle")}</strong><span>{t("sharedPlans.execution.noPlansDescription")}</span></div> : null}
    {repositories.length === 0 ? <div className="run-setup-edge-state" role="status"><strong>{t("sharedPlans.execution.noRepositoriesTitle")}</strong><span>{t("sharedPlans.execution.noRepositoriesDescription")}</span></div> : null}

    <div className="shared-plan-workspace__core">
      {!planKey ? <section className="template-plan-choice" aria-labelledby="template-plan-choice-title">
        <div><span className="eyebrow">{t("sharedPlans.templateEyebrow")}</span><h3 id="template-plan-choice-title">{t("sharedPlans.templateTitle")}</h3><p>{t("sharedPlans.templatePrimaryHelp")}</p></div>
        {catalog.length > 0 ? <div className="shared-plan-workspace__plan"><label htmlFor="shared-plan-saved-plan">{t("sharedPlans.savedPlanChoice")}</label><select id="shared-plan-saved-plan" value="" onChange={(event) => { setPlanKey(event.target.value); setSubmission({ state: "idle" }); }}>
          <option value="">{t("sharedPlans.selectSavedPlan")}</option>
          {catalog.map((plan) => <option key={plan.plan_key} value={plan.plan_key}>{plan.plan_key}</option>)}
        </select></div> : <p className="template-plan-choice__description">{t("sharedPlans.noSavedPlansPrimary")}</p>}
        {onOpenPlans ? <button className="button button--secondary" type="button" onClick={onOpenPlans}>{t("sharedPlans.execution.managePlans")}</button> : null}
      </section> : null}

      {planKey ? <section className="shared-run-stage shared-run-stage--plan">
        <div className="shared-plan-selected" role="group" aria-label={t("sharedPlans.savedPlan")}>
          <div className="shared-plan-selected__identity"><span className="eyebrow">{t("sharedPlans.templateEyebrow")}</span><strong>{selectedPlan?.plan_key ?? planKey}</strong><span>{t("sharedPlans.savedPlan")}</span></div>
          <div className="shared-plan-selected__actions"><details className="shared-plan-lock-preview">
            <summary>{t("sharedPlans.goonLock")}</summary>
            <div className="shared-plan-lock-preview__content">
              <p className="muted">{t("sharedPlans.goonLockHelp")}</p>
              {lockPreviewing ? <p className="muted loading-state" role="status">{t("sharedPlans.goonLockLoading")}</p> : null}
              {lockPreviewError ? <p className="notice notice--warning" role="alert">{lockPreviewError}</p> : null}
              {lockPreview ? <pre><code>{lockPreview.content}</code></pre> : null}
            </div>
          </details><button className="button button--quiet button--small" type="button" disabled={actionsDisabled} onClick={() => { setPlanKey(""); setSubmission({ state: "idle" }); }}>{t("sharedPlans.changePlan")}</button></div>
        </div>
        {planLoadError ? <div className="run-setup-edge-state run-setup-edge-state--error" role="alert"><strong>{planLoadError}</strong><button className="text-button" type="button" onClick={() => { void planDocumentRead.refresh(); }}>{t("sharedPlans.inputs.retryLoad")}</button></div> : null}
        {selectedPlan?.changed_since_last_run ? <div className="notice notice--warning" role="status"><strong>{t("sharedPlans.execution.planChangedTitle")}</strong><p>{t("sharedPlans.execution.planChangedMessage", { name: selectedPlan.plan_key })}</p></div> : null}
        {inputDefinitions.length > 0 ? <ExecutionPlanInputFields definitions={inputDefinitions} values={inputValues} onChange={(name, value) => { setInputValues((current) => ({ ...current, [name]: value })); setRunAttemptError(null); setSubmission({ state: "idle" }); }} /> : null}
        <SharedPlanAgentFields draft={agentDraft} onChange={(draft) => { setAgentDraft(draft); setRunAttemptError(null); setSubmission({ state: "idle" }); }} />
        {!agentReady ? <p role="status">{t("sharedPlans.agent.completeSelection")}</p> : null}
        <PlanInputDiagnostic inspection={inputInspection} />
      </section> : null}
      {selectedPlanDocument ? <RepositorySelectionPanel repositories={repositories} selectedIds={selectedIds} onChange={chooseIds} /> : null}
    </div>

    <BackendVerification
      preview={preview}
      previewing={previewing}
      error={previewError}
      selectedCount={selectedIds.length}
      onRetry={() => { void previewRead.refresh(); }}
      onRemoveRepository={(repositoryId) => chooseIds(selectedIds.filter((id) => id !== repositoryId))}
      revealProblemsRevision={blockedAttemptRevision}
    />

    {currentRunAttemptError ? <div className="notice notice--danger shared-plan-run-failure" role="alert"><strong>{currentRunAttemptError.title}</strong><p>{currentRunAttemptError.message}</p></div> : null}
    {currentSubmission.state === "accepted" ? <div className="notice notice--success shared-plan-run-accepted" role="status" aria-live="polite"><strong>{t("sharedPlans.verification.acceptedTitle")}</strong><p>{t("sharedPlans.verification.acceptedMessage")}</p></div> : null}
    {currentSubmission.state === "rejected" ? <div className="notice notice--danger shared-plan-run-failure" role="alert"><strong>{currentSubmission.title}</strong><p>{currentSubmission.message}</p></div> : null}
    {repairHandoff ? <div className="notice" role="status"><strong>{t("sharedPlans.agent.handoff")} <code>{repairHandoff.id}</code></strong>
      {repairHandoff.result ? <p>{t("gameTape.campaign.result")}: {repairHandoff.result.result}</p> : null}
      {repairHandoff.result?.journal_revision === null ? <p>{t("sharedPlans.agent.unrecorded")}</p> : null}
      {repairHandoff.result?.invocation_result ? <p>{t("sharedPlans.agent.invocationStopped", { reason: repairHandoff.result.invocation_result.stop_reason })}</p> : null}
      {repairHandoff.uncertain ? <p>{t("sharedPlans.agent.transportUncertain")}</p> : null}
      {onOpenCampaign && repairHandoff.result?.journal_revision !== null ? <button className="button button--secondary" type="button" onClick={() => onOpenCampaign(repairHandoff.id)}>{t("gameTape.campaign.open")}</button> : null}
    </div> : null}

    {repairSelection ? <label className="checkbox-row"><input type="checkbox" checked={repairAuthorized} disabled={!canAttemptRun || submissionPending} onChange={(event) => setRepairAuthorization(event.target.checked ? currentRunOwner : null)} />{t("sharedPlans.agent.authorize")}</label> : null}

    {planKey ? <div className={preview?.blocked_count ? "shared-plan-run-bar shared-plan-run-bar--blocked" : preview ? "shared-plan-run-bar shared-plan-run-bar--ready" : "shared-plan-run-bar"}>
      <div className="shared-plan-run-bar__status" role="status" aria-live="polite"><span className="shared-plan-run-bar__state-dot" aria-hidden="true" />
        {submitting ? t("sharedPlans.verification.submitting")
          : planLoadError ? t("sharedPlans.verification.unavailable")
          : !selectedPlanDocument ? t("sharedPlans.inputs.loading")
          : !requiredInputsComplete ? t("sharedPlans.inputs.completeRequired")
          : selectedIds.length === 0 ? t("sharedPlans.verification.chooseRepositories")
          : previewing ? t("sharedPlans.verification.checking")
          : previewError ? t("sharedPlans.verification.unavailable")
          : preview ? t("sharedPlans.verification.summary", { ready: preview.ready_count, blocked: preview.blocked_count })
          : t("sharedPlans.verification.checking")}
      </div>
      <button
        className="button button--primary"
        type="button"
        disabled={actionsDisabled || submissionPending || !canAttemptRun || (repairSelection !== null && (!repairAuthorized || !repairReady))}
        onClick={() => { void submitRun(); }}
      >{submitting ? t("sharedPlans.verification.submittingButton") : repairSelection ? t("sharedPlans.agent.runRepair") : t("sharedPlans.run", { count: selectedIds.length })}</button>
    </div> : null}

    {lineupDetached ? <p className="notice notice--warning" role="status">{t("sharedPlans.lineupServerRemoved")}</p> : null}
    {lineupEditorStale ? <div className="notice notice--warning" role="status"><p>{t("sharedPlans.lineupServerChanged")}</p><button className="button button--secondary button--small" type="button" disabled={lineupInspection.resource.status !== "read-fresh" || lineupInspection.resource.data === null} onClick={loadLatestServerLineup}>{t("sharedPlans.loadLatestServerVersion")}</button></div> : null}
    {message && sameIdentityIncarnation(message.owner, lineupOwnerRef.current) ? <p className="muted" role="status" aria-live="polite">{message.text}</p> : null}

    <details className="advanced-disclosure shared-plan-management"><summary>{t("sharedPlans.execution.advanced")}</summary>
      <details className="advanced-disclosure" onToggle={(event) => setSavedSelectionsOpen(event.currentTarget.open)}><summary>{t("sharedPlans.execution.savedSelections")}</summary>
        <p className="muted">{t("sharedPlans.execution.savedSelectionsHelp")}</p>
        <div className="form-grid">
          <label><span>{t("sharedPlans.lineup")}</span><select value={lineupKey} onChange={(event) => loadLineup(event.target.value)}>
            <option value="">{t("sharedPlans.selectLineup")}</option>
            {lineupCatalog.map((lineup) => <option key={lineup.lineup_key} value={lineup.lineup_key}>{lineup.name} ({lineup.team_count})</option>)}
          </select></label>
          <label><span>{t("sharedPlans.lineupKey")}</span><input value={lineupDraftKey} onChange={(event) => setLineupDraftKey(event.target.value)} placeholder="backend" /></label>
          <label><span>{t("sharedPlans.lineupName")}</span><input value={lineupName} onChange={(event) => setLineupName(event.target.value)} placeholder="Backend repositories" /></label>
        </div>
        <button className="button button--secondary" type="button" disabled={actionsDisabled || !canSaveLineup || !lineupDraftKey.trim() || !lineupName.trim() || selectedPaths.length === 0} onClick={() => void saveLineup()}>{t("sharedPlans.saveSelected", { count: selectedPaths.length })}</button>
      </details>
    </details>
  </section>;
}

const SHARED_PREVIEW_PAGE_SIZE = 100;

function BackendVerification({ preview, previewing, error, selectedCount, onRetry, onRemoveRepository, revealProblemsRevision }: { preview: SharedPlanPreview | null; previewing: boolean; error: string | null; selectedCount: number; onRetry: () => void; onRemoveRepository: (repositoryId: string) => void; revealProblemsRevision: number }) {
  const { t } = useTranslation();
  const [blockerPage, setBlockerPage] = useState(0);
  const [detailPage, setDetailPage] = useState(0);
  const [problemsOpen, setProblemsOpen] = useState(revealProblemsRevision > 0);
  const previewTeams = preview?.teams ?? [];
  const blockerCount = previewTeams.filter((entry) => "reason" in entry).length;
  const blockerPageCount = Math.max(1, Math.ceil(blockerCount / SHARED_PREVIEW_PAGE_SIZE));
  const boundedBlockerPage = Math.min(blockerPage, blockerPageCount - 1);
  const detailPageCount = Math.max(1, Math.ceil(previewTeams.length / SHARED_PREVIEW_PAGE_SIZE));
  const boundedDetailPage = Math.min(detailPage, detailPageCount - 1);

  useEffect(() => {
    if (revealProblemsRevision > 0) setProblemsOpen(true);
  }, [revealProblemsRevision]);
  useEffect(() => { if (blockerPage !== boundedBlockerPage) setBlockerPage(boundedBlockerPage); }, [blockerPage, boundedBlockerPage]);
  useEffect(() => { if (detailPage !== boundedDetailPage) setDetailPage(boundedDetailPage); }, [detailPage, boundedDetailPage]);
  if (selectedCount === 0) return null;
  if (previewing) return <section className="backend-verification backend-verification--checking shared-run-stage shared-run-stage--verification" role="status"><div><span className="eyebrow">{t("sharedPlans.verification.title")}</span><strong>{t("sharedPlans.verification.checking")}</strong></div><span>{t("sharedPlans.verification.checkingHelp")}</span></section>;
  if (error) return <section className="backend-verification backend-verification--error shared-run-stage shared-run-stage--verification" role="alert"><div><span className="eyebrow">{t("sharedPlans.verification.title")}</span><strong>{error}</strong></div><button className="button button--secondary button--small" type="button" onClick={onRetry}>{t("sharedPlans.verification.retry")}</button></section>;
  if (!preview) return null;
  const blockers = previewTeams.filter((entry) => "reason" in entry);
  const blockerStart = boundedBlockerPage * SHARED_PREVIEW_PAGE_SIZE;
  const blockerEntries = blockers.slice(blockerStart, blockerStart + SHARED_PREVIEW_PAGE_SIZE);
  const detailStart = boundedDetailPage * SHARED_PREVIEW_PAGE_SIZE;
  const detailEntries = previewTeams.slice(detailStart, detailStart + SHARED_PREVIEW_PAGE_SIZE);
  return <section className={preview.blocked_count > 0 ? "backend-verification backend-verification--result backend-verification--blocked shared-run-stage shared-run-stage--verification" : "backend-verification backend-verification--result backend-verification--ready shared-run-stage shared-run-stage--verification"} aria-live="polite">
    <div className="backend-verification__headline"><span className="eyebrow">{t("sharedPlans.verification.title")}</span><strong>{preview.blocked_count > 0 ? t("sharedPlans.verification.summary", { ready: preview.ready_count, blocked: preview.blocked_count }) : t("sharedPlans.verification.ready", { count: preview.ready_count })}</strong></div>
    {preview.blocked_count > 0 ? <details className="backend-verification__problems" open={problemsOpen} onToggle={(event) => setProblemsOpen(event.currentTarget.open)}>
      <summary>{t("sharedPlans.verification.showProblems", { count: preview.blocked_count })}</summary>
      <ol>{blockerEntries.map((entry) => <li key={entry.repository.repository_id}>
        <div><strong>{entry.repository.name}</strong><small>{entry.repository.path}</small><span>{entry.reason}</span>
          <small><code>{entry.diagnostic.reason_code}</code> · {entry.diagnostic.authority_owner}</small>
          <small>{t("sharedPlans.verification.whyNotState", { current: entry.diagnostic.current_state, required: entry.diagnostic.required_state })}</small>
          {entry.diagnostic.safe_next_action ? <small>{t("sharedPlans.verification.safeNextAction", { action: entry.diagnostic.safe_next_action })}</small> : null}
        </div>
        <button className="button button--quiet button--small" type="button" onClick={() => onRemoveRepository(entry.repository.repository_id)}>{t("sharedPlans.verification.removeRepository")}</button>
      </li>)}</ol>
      {blockers.length > SHARED_PREVIEW_PAGE_SIZE ? <nav className="shared-preview-pagination" aria-label={t("sharedPlans.verification.problemPaginationLabel")}><button className="button button--secondary button--small" type="button" disabled={boundedBlockerPage === 0} onClick={() => setBlockerPage(Math.max(0, boundedBlockerPage - 1))}>{t("common.previous")}</button><span>{t("sharedPlans.repositorySelection.pageRange", { from: blockerStart + 1, to: Math.min(blockerStart + blockerEntries.length, blockers.length), total: blockers.length })}</span><button className="button button--secondary button--small" type="button" disabled={boundedBlockerPage + 1 >= blockerPageCount} onClick={() => setBlockerPage(Math.min(blockerPageCount - 1, boundedBlockerPage + 1))}>{t("common.next")}</button></nav> : null}
    </details> : null}
    <details className="advanced-disclosure"><summary>{t("sharedPlans.verification.details")}</summary>
      <div className="notice notice--scope"><strong>{t("sharedPlans.verification.executionPreview")}</strong><p><code>{preview.plan_id}</code></p><small>{t("sharedPlans.previewRunContract")}</small></div>
      <div className="action-row"><button className="button button--secondary button--small" type="button" onClick={() => exportSharedPlanPreview(preview)}>{t("sharedPlans.verification.exportJson")}</button></div>
      <details><summary>{t("sharedPlans.verification.readinessFacts")}</summary><dl className="game-tape-summary__facts">{(preview.readiness ?? []).map((fact) => <div key={`${fact.authority_owner}:${fact.reason_code}`}><dt>{fact.authority_owner}</dt><dd><strong>{fact.state}</strong> · <code>{fact.reason_code}</code><br />{fact.current_state}<br /><small>{t("sharedPlans.verification.requiredState", { required: fact.required_state })}</small>{fact.safe_next_action ? <><br /><small>{t("sharedPlans.verification.safeNextAction", { action: fact.safe_next_action })}</small></> : null}</dd></div>)}</dl></details>
      {(preview.explicit_inputs ?? []).length > 0 ? <dl className="game-tape-summary__facts">{(preview.explicit_inputs ?? []).map((input) => <div key={input.name}><dt>{input.name}</dt><dd><code>{JSON.stringify(input.value)}</code></dd></div>)}</dl> : <p className="muted">{t("sharedPlans.verification.noExplicitInputs")}</p>}
      <ul className="compact-list">{detailEntries.map((entry) => <li key={entry.repository.repository_id}><strong>{entry.state}</strong> {entry.repository.name}{"preview" in entry ? <>
        <small>{t("sharedPlans.verification.planPolicy", { purpose: entry.preview.execution_purpose ?? "—", mode: entry.preview.repository_execution_mode ?? "—" })}</small>
        <small>{t("sharedPlans.stepsReady", { count: entry.preview.steps.length })}</small>
        <details><summary>{t("sharedPlans.verification.stepPolicy")}</summary><ol className="compact-list">{entry.preview.steps.map((step) => <li key={step.step_id}><code>{step.step_id}</code><small>{t("sharedPlans.verification.stepPolicyLine", { disposition: step.disposition, timeout: step.timeout_seconds ?? "—", workspace: step.workspace_source ?? "repository", cache: step.cache_source ?? "empty", egress: step.egress_mode ?? "none" })}</small>{step.reason ? <small>{step.reason}</small> : null}</li>)}</ol></details>
      </> : <small>{entry.reason}</small>}</li>)}</ul>
      {preview.teams.length > SHARED_PREVIEW_PAGE_SIZE ? <nav className="shared-preview-pagination" aria-label={t("sharedPlans.verification.detailPaginationLabel")}><button className="button button--secondary button--small" type="button" disabled={boundedDetailPage === 0} onClick={() => setDetailPage(Math.max(0, boundedDetailPage - 1))}>{t("common.previous")}</button><span>{t("sharedPlans.repositorySelection.pageRange", { from: detailStart + 1, to: Math.min(detailStart + detailEntries.length, preview.teams.length), total: preview.teams.length })}</span><button className="button button--secondary button--small" type="button" disabled={boundedDetailPage + 1 >= detailPageCount} onClick={() => setDetailPage(Math.min(detailPageCount - 1, boundedDetailPage + 1))}>{t("common.next")}</button></nav> : null}
    </details>
  </section>;
}

function exportSharedPlanPreview(preview: SharedPlanPreview): void {
  const blob = new Blob([`${JSON.stringify(preview, null, 2)}\n`], { type: "application/json" });
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = `${preview.plan_key}-execution-preview.json`;
  anchor.click();
  URL.revokeObjectURL(href);
}

function validateSavedLineupIdentity(
  result: WriteCommandResult<LeagueLineupDocument>,
  expectedLineupKey: string,
): WriteCommandResult<LeagueLineupDocument> {
  if (result.outcome !== "saved" || result.value.lineup_key === expectedLineupKey) return result;
  const diagnostic = appDiagnosticStore.record({
    source: "league-lineup-save-identity",
    error: new Error(`Lineup mutation identity mismatch: expected ${expectedLineupKey}, received ${result.value.lineup_key}`),
    scope: "league-lineups",
    causalityRevision: result.causalityRevision,
  });
  return { outcome: "write-error", diagnosticId: diagnostic.id, recovery: "refresh" };
}
