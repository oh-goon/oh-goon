import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import { advanceIdentityIncarnation, sameIdentityIncarnation, type IdentityIncarnation } from "@/domain/concurrency/identityIncarnation";

import type { LeagueMap, SharedPlanCatalog, SharedPlanScheduleCreateRequest } from "@generated/models";
import { RepositorySelectionPanel, useSharedPlanDocument, useSharedPlanLockPreview } from "@/features/league/LeagueFeature";
import {
  ExecutionPlanInputFields,
  executionPlanInputs,
  initialExecutionPlanInputValues,
  requiredExecutionPlanInputsComplete,
} from "@/features/plans/PlansFeature";
import { ScheduleFrequencyFields, type ScheduleFrequencyChoice } from "@/features/schedules/components/ScheduleFrequencyFields";
import { scheduleFrequencyMinutes, scheduleKeyFromName } from "@/features/schedules/model";
import { editSchedulePlanInput, schedulePlanInputValues, type SchedulePlanInputDraft } from "@/features/schedules/components/schedulePlanInputDraft";

interface Props {
  league: LeagueMap | null;
  plans: SharedPlanCatalog | null;
  actionsDisabled: boolean;
  onOpenRepositories?: () => void;
  onOpenPlans?: () => void;
  onCreate: (scheduleId: string, request: SharedPlanScheduleCreateRequest) => Promise<boolean>;
  onCreated?: () => void;
}

export function ScheduleCreatePanel({ league, plans, actionsDisabled, onOpenRepositories, onOpenPlans, onCreate, onCreated }: Props) {
  const { t } = useTranslation();
  const teams = league?.teams ?? [];
  const savedPlans = plans?.plans ?? [];
  const availableRepositoryIds = useMemo(
    () => new Set(teams.map((entry) => entry.repository.repository_id)),
    [teams],
  );
  const [scheduleId, setScheduleId] = useState("");
  const [idTouched, setIdTouched] = useState(false);
  const [name, setName] = useState("");
  const [repositoryIds, setRepositoryIds] = useState<string[]>([]);
  const [planKey, setPlanKey] = useState("");
  const [inputDraft, setInputDraft] = useState<SchedulePlanInputDraft | null>(null);
  const [frequency, setFrequency] = useState<ScheduleFrequencyChoice>("daily");
  const [customInterval, setCustomInterval] = useState("60");
  const [savingFor, setSavingFor] = useState<IdentityIncarnation | null>(null);
  const editorOwnerRef = useRef<IdentityIncarnation>({ identity: "", generation: 0 });

  useEffect(() => {
    setRepositoryIds((current) => current.filter((repositoryId) => availableRepositoryIds.has(repositoryId)));
  }, [availableRepositoryIds]);
  useEffect(() => {
    if (planKey && !savedPlans.some((plan) => plan.plan_key === planKey)) setPlanKey("");
  }, [planKey, savedPlans]);
  const planDocumentRead = useSharedPlanDocument(planKey);
  const planDocument = planDocumentRead.resource.data;
  const planInputsLoading = planDocumentRead.resource.status === "read-loading" || planDocumentRead.resource.status === "read-refreshing";
  const planInputsError = planDocumentRead.resource.failure?.message ?? null;

  // Do not allow the previous Plan's document or inputs to authorize this selection.
  const selectedDocument = planDocument?.plan_key === planKey && planKey ? planDocument : null;
  const documentIdentity = selectedDocument
    ? canonicalIdentity({ planKey, sha256: selectedDocument.sha256 }) : null;
  const defaultInputValues = useMemo(
    () => initialExecutionPlanInputValues(selectedDocument?.inputs ?? []),
    // A document refresh may return a new object; a revision-bound user draft wins.
    [selectedDocument],
  );
  const inputValues = schedulePlanInputValues(documentIdentity, defaultInputValues, inputDraft);
  const inputDefinitions = selectedDocument?.inputs ?? [];
  const submittedPlanInputs = useMemo(() => executionPlanInputs(inputValues), [inputValues]);
  const planInputsReady = selectedDocument !== null
    && requiredExecutionPlanInputsComplete(inputDefinitions, inputValues);
  const validationRead = useSharedPlanLockPreview(
    planKey,
    selectedDocument?.sha256 ?? "",
    submittedPlanInputs,
    Boolean(planInputsReady),
    t("schedules.create.planInputsInvalid"),
  );
  const planInputsValidating = validationRead.resource.status === "read-loading" || validationRead.resource.status === "read-refreshing";
  const planInputsValidationError = validationRead.resource.failure?.message ?? null;
  const planDocumentFresh = selectedDocument !== null && planDocumentRead.resource.status === "read-fresh";
  const planValidationFresh = validationRead.resource.status === "read-fresh";
  const planInputsVerified = planInputsReady && planDocumentFresh && planValidationFresh && validationRead.resource.data !== null;

  const intervalMinutes = scheduleFrequencyMinutes(frequency, customInterval);
  const selectedPlan = savedPlans.find((plan) => plan.plan_key === planKey);
  const frequencyLabel = frequency === "custom"
    ? t("schedules.frequency.minutes", { count: Number(customInterval) || 0 })
    : t(`schedules.frequency.${frequency}`);
  const valid = scheduleId.trim().length > 0 && name.trim().length > 0 && repositoryIds.length > 0
    && selectedPlan !== undefined && planInputsVerified && Number.isInteger(intervalMinutes) && intervalMinutes >= 1 && intervalMinutes <= 10080;

  const selectedRepositoryInstanceIds = Object.fromEntries(
    teams
      .filter((team) => repositoryIds.includes(team.repository.repository_id))
      .map((team) => [team.repository.repository_id, team.repository_instance_id]),
  );
  const editorIdentity = canonicalIdentity({
    scheduleId, name, repositoryIds: [...repositoryIds].sort(), repositoryInstanceIds: selectedRepositoryInstanceIds, planKey,
    planRevision: selectedDocument?.sha256 ?? null, resolvedPlanId: validationRead.resource.data?.plan_id ?? null, executionDigest: validationRead.resource.data?.execution_digest ?? null, frequency, customInterval, inputs: inputValues,
  });
  editorOwnerRef.current = advanceIdentityIncarnation(editorOwnerRef.current, editorIdentity);
  const saving = savingFor !== null && sameIdentityIncarnation(savingFor, editorOwnerRef.current);
  const createPending = savingFor !== null;

  const changeName = (value: string) => {
    setName(value);
    if (!idTouched) setScheduleId(scheduleKeyFromName(value));
  };
  const create = async () => {
    if (!valid) return;
    const commandOwner = editorOwnerRef.current;
    setSavingFor(commandOwner);
    try {
      const saved = await onCreate(scheduleId.trim(), {
        name: name.trim(),
        repository_ids: [...repositoryIds].sort(),
        expected_repository_instance_ids: selectedRepositoryInstanceIds,
        plan_key: planKey,
        expected_plan_revision: selectedDocument!.sha256,
        expected_plan_id: validationRead.resource.data!.plan_id,
        expected_execution_digest: validationRead.resource.data!.execution_digest,
        interval_minutes: intervalMinutes,
        state: "active",
        inputs: executionPlanInputs(inputValues),
      });
      if (saved && sameIdentityIncarnation(editorOwnerRef.current, commandOwner)) {
        setScheduleId(""); setIdTouched(false); setName(""); setRepositoryIds([]);
        onCreated?.();
      }
    } finally {
      setSavingFor((current) => current !== null && sameIdentityIncarnation(current, commandOwner) ? null : current);
    }
  };

  return <section className="panel schedule-create-panel">
    <div className="section-heading"><div><span className="eyebrow">{t("schedules.create.eyebrow")}</span><h2>{t("schedules.create.title")}</h2><p>{t("schedules.create.description")}</p></div></div>
    {teams.length === 0 ? <div className="run-setup-edge-state" role="status"><strong>{t("schedules.create.noRepositoriesTitle")}</strong><span>{t("schedules.create.noRepositoriesDescription")}</span>{onOpenRepositories ? <button className="text-button" type="button" onClick={onOpenRepositories}>{t("schedules.create.openRepositories")}</button> : null}</div> : null}
    {savedPlans.length === 0 ? <div className="run-setup-edge-state" role="status"><strong>{t("schedules.create.noPlansTitle")}</strong><span>{t("schedules.create.noPlansDescription")}</span>{onOpenPlans ? <button className="text-button" type="button" onClick={onOpenPlans}>{t("schedules.create.openPlans")}</button> : null}</div> : null}
    <div className="form-grid schedule-create-grid schedule-create-step">
      <label><span>{t("schedules.create.name")}</span><input required value={name} onChange={(event) => changeName(event.target.value)} placeholder={t("schedules.create.namePlaceholder")} /></label>
      <label><span>{t("schedules.create.plan")}</span><select required value={planKey} onChange={(event) => { setPlanKey(event.target.value); setInputDraft(null); }}><option value="">{t("schedules.create.selectPlan")}</option>{savedPlans.map((plan) => <option key={plan.plan_key} value={plan.plan_key}>{plan.plan_key}</option>)}</select></label>
      <ScheduleFrequencyFields frequency={frequency} customInterval={customInterval} frequencyLabelKey="schedules.create.frequency" intervalLabelKey="schedules.create.interval" onFrequencyChange={setFrequency} onCustomIntervalChange={setCustomInterval} />
    </div>
    {planKey && planInputsLoading ? <p className="loading-state" role="status" aria-live="polite">{t("sharedPlans.inputs.loading")}</p> : null}
    {planKey && planInputsError ? <div className="run-setup-edge-state run-setup-edge-state--error" role="alert"><strong>{planInputsError}</strong><button className="text-button" type="button" onClick={() => { void planDocumentRead.refresh(); }}>{t("schedules.create.retryPlanInputs")}</button></div> : null}
    {selectedDocument ? <ExecutionPlanInputFields definitions={inputDefinitions} values={inputValues} onChange={(inputName, value) => {
      if (documentIdentity === null) return;
      setInputDraft((current) => editSchedulePlanInput(documentIdentity, defaultInputValues, current, inputName, value));
    }} /> : null}
    {selectedDocument && !planInputsReady ? <small className="muted" role="status">{t("schedules.create.completePlanInputs")}</small> : null}
    {planInputsValidating ? <small className="muted loading-state" role="status" aria-live="polite">{t("schedules.create.validatingPlanInputs")}</small> : null}
    {planInputsValidationError ? <div className="run-setup-edge-state run-setup-edge-state--error" role="alert"><strong>{planInputsValidationError}</strong><button className="text-button" type="button" onClick={() => { void validationRead.refresh(); }}>{t("schedules.create.retryPlanValidation")}</button></div> : null}
    {teams.length > 0 ? <div className="schedule-create-step schedule-create-repositories"><RepositorySelectionPanel repositories={teams} selectedIds={repositoryIds} onChange={setRepositoryIds} /></div> : null}
    {name.trim() && repositoryIds.length > 0 && selectedPlan && planInputsVerified ? <div className="schedule-create-review" role="status"><span className="eyebrow">{t("schedules.create.reviewEyebrow")}</span><strong>{name.trim()}</strong><p>{t("schedules.create.review", { count: repositoryIds.length, plan: selectedPlan.plan_key, frequency: frequencyLabel })}</p><small>{t("schedules.create.startsEnabled")}</small></div> : null}
    <details className="advanced-disclosure schedule-technical-id"><summary>{t("schedules.create.technicalId")}</summary><p className="muted">{t("schedules.create.technicalIdHelp")}</p><p className="schedule-id-preview">{t("schedules.create.generatedId")} <code>{scheduleId || "—"}</code></p><label><span>{t("schedules.create.id")}</span><input value={scheduleId} onChange={(event) => { setIdTouched(true); setScheduleId(event.target.value); }} placeholder="nightly-quality" /></label></details>
    <details className="advanced-disclosure schedule-contract"><summary>{t("schedules.create.contractTitle")}</summary><p className="muted">{t("schedules.create.contractMessage")}</p></details>
    <div className="button-row schedule-create-actions"><button className="button button--primary" type="button" disabled={actionsDisabled || createPending || !valid} onClick={() => void create()}>{saving ? t("schedules.create.creating") : t("schedules.create.action")}</button></div>
    {!valid && teams.length > 0 && savedPlans.length > 0 ? <small className="muted">{t("schedules.create.completeFields")}</small> : null}
  </section>;
}
