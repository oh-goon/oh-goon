import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient, useReportMutationFeedback } from "@/app/ApiClientProvider";
import { appDiagnosticStore } from "@/domain/diagnostics/appDiagnosticStore";
import { usePlanTemplates } from "@/features/league/hooks/usePlanTemplates";
import { useSharedPlanDocument } from "@/features/league/hooks/useSharedPlanDocument";
import { useSharedPlans } from "@/features/league/hooks/useSharedPlans";
import { editorWriteAuthority, templateReplaySafe } from "@/features/league/writeAuthority";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import { advanceIdentityIncarnation, sameIdentityIncarnation, type IdentityIncarnation } from "@/domain/concurrency/identityIncarnation";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";
import type { SharedPlanDocument } from "@generated/models";

export function SavedPlanManager({ actionsDisabled }: { actionsDisabled: boolean }) {
  const { t } = useTranslation();
  const client = useApiClient();
  const reportPlanMutation = useReportMutationFeedback("plans");
  const plans = useSharedPlans();
  const templates = usePlanTemplates();
  const catalog = plans.resource.data?.plans ?? [];
  const templateCatalog = templates.resource.data?.templates ?? [];
  const [planKey, setPlanKey] = useState("");
  const [draftKey, setDraftKey] = useState("");
  const [draft, setDraft] = useState("");
  const [draftBaseRevision, setDraftBaseRevision] = useState<string | null>(null);
  const [templateKey, setTemplateKey] = useState("");
  const [templateSha, setTemplateSha] = useState<string | null>(null);
  const selectedTemplate = templateCatalog.find((template) => template.template_key === templateKey);
  const selectedTemplateStable = selectedTemplate !== undefined && selectedTemplate.sha256 === templateSha;
  const [message, setMessage] = useState<{ owner: IdentityIncarnation; text: string } | null>(null);
  const [draftDetached, setDraftDetached] = useState(false);
  const selectedPlan = useSharedPlanDocument(planKey);
  const observedPlanRevision = selectedPlan.resource.data?.sha256 ?? null;
  const editorStale = Boolean(planKey && draftBaseRevision !== null && observedPlanRevision !== null && observedPlanRevision !== draftBaseRevision);
  const draftTargetKey = draftKey.trim();
  const canSaveDraft = editorWriteAuthority({
    catalogFresh: plans.resource.status === "read-fresh",
    targetKey: draftTargetKey,
    ownedKey: planKey,
    targetReadFresh: selectedPlan.resource.status === "read-fresh",
    existingKeys: catalog.map((plan) => plan.plan_key),
    editorStale,
  });
  const templateAuthorityFresh = templates.resource.status === "read-fresh" && plans.resource.status === "read-fresh";
  const templateDestinationKey = selectedTemplate?.template_key.replace("/", "-") ?? "";
  const templateDestination = catalog.find((plan) => plan.plan_key === templateDestinationKey);
  const canCreateTemplate = templateReplaySafe(selectedTemplate, templateDestination);
  const editorOwnerRef = useRef<IdentityIncarnation>({ identity: "", generation: 0 });
  const editorIdentity = canonicalIdentity({ planKey, draftKey, draft, draftBaseRevision, templateKey, templateSha });
  editorOwnerRef.current = advanceIdentityIncarnation(editorOwnerRef.current, editorIdentity);

  useEffect(() => {
    if (planKey && plans.resource.status === "read-fresh" && !catalog.some((plan) => plan.plan_key === planKey)) {
      setPlanKey("");
      setDraftBaseRevision(null);
      setDraftDetached(true);
      setMessage(null);
    }
  }, [catalog, planKey, plans.resource.status]);

  useEffect(() => {
    if (editorStale) setMessage(null);
  }, [editorStale]);

  useEffect(() => {
    if (!templateKey) return;
    if (!selectedTemplate || selectedTemplate.sha256 !== templateSha) {
      setTemplateKey("");
      setTemplateSha(null);
      setMessage(null);
    }
  }, [selectedTemplate, templateKey, templateSha]);

  useEffect(() => {
    const document = selectedPlan.resource.data;
    if (!document) return;
    if (draftKey === document.plan_key && draftBaseRevision !== null) return;
    setDraftKey(document.plan_key);
    setDraft(document.content);
    setDraftBaseRevision(document.sha256);
  }, [draftBaseRevision, draftKey, selectedPlan.resource.data]);

  const createNew = () => {
    setPlanKey("");
    setDraftKey("");
    setDraft("version: 1\ngoons:\n- python/quality\n");
    setDraftBaseRevision(null);
    setDraftDetached(false);
    setMessage(null);
  };

  const loadLatestServerPlan = () => {
    const document = selectedPlan.resource.data;
    if (!document || document.plan_key !== planKey) return;
    setDraftKey(document.plan_key);
    setDraft(document.content);
    setDraftBaseRevision(document.sha256);
    setDraftDetached(false);
    setMessage(null);
  };

  const createFromTemplate = async () => {
    if (!selectedTemplate || !selectedTemplateStable || !templateAuthorityFresh || !canCreateTemplate || actionsDisabled) return;
    const commandOwner = editorOwnerRef.current;
    const result = await client.createSharedPlanFromTemplate(selectedTemplate.template_key, selectedTemplate.sha256);
    reportPlanMutation(result);
    if (result.outcome === "saved" && sameIdentityIncarnation(editorOwnerRef.current, commandOwner)) {
      setPlanKey(result.value.plan_key);
      setDraftKey(result.value.plan_key);
      setDraft(result.value.content);
      setDraftBaseRevision(result.value.sha256);
      setDraftDetached(false);
      const committedOwner = canonicalIdentity({
        planKey: result.value.plan_key,
        draftKey: result.value.plan_key,
        draft: result.value.content,
        draftBaseRevision: result.value.sha256,
        templateKey,
        templateSha,
      });
      setMessage({ owner: advanceIdentityIncarnation(editorOwnerRef.current, committedOwner), text: t("sharedPlans.templateReady", { name: result.value.plan_key }) });
    }
  };

  const save = async () => {
    const key = draftKey.trim();
    if (!key || !draft.trim() || actionsDisabled || !canSaveDraft) return;
    const expectedRevision = key === planKey ? draftBaseRevision : null;
    const commandOwner = editorOwnerRef.current;
    const result = validateSavedPlanIdentity(await client.saveSharedPlan(key, draft, expectedRevision), key);
    reportPlanMutation(result);
    if (result.outcome === "saved" && sameIdentityIncarnation(editorOwnerRef.current, commandOwner)) {
      setPlanKey(result.value.plan_key);
      setDraftKey(result.value.plan_key);
      setDraft(result.value.content);
      setDraftBaseRevision(result.value.sha256);
      setDraftDetached(false);
      const committedOwner = canonicalIdentity({
        planKey: result.value.plan_key,
        draftKey: result.value.plan_key,
        draft: result.value.content,
        draftBaseRevision: result.value.sha256,
        templateKey,
        templateSha,
      });
      setMessage({ owner: advanceIdentityIncarnation(editorOwnerRef.current, committedOwner), text: t("sharedPlans.saved", { name: result.value.plan_key }) });
    }
  };

  const loading = plans.resource.status === "read-loading"
    || plans.resource.status === "read-refreshing"
    || templates.resource.status === "read-loading"
    || templates.resource.status === "read-refreshing";
  const error = plans.resource.failure?.message ?? templates.resource.failure?.message ?? selectedPlan.resource.failure?.message ?? null;

  return <section className="panel saved-plan-manager" aria-label={t("plans.title")}>
    {loading && plans.resource.data === null ? <p className="muted loading-state" role="status">{t("sharedPlans.loading")}</p> : null}
    {error ? <p className="error-text" role="alert">{error}</p> : null}

    <div className="form-grid">
      <label><span>{t("sharedPlans.savedPlanChoice")}</span><select value={planKey} onChange={(event) => { setPlanKey(event.target.value); setDraftKey(""); setDraft(""); setDraftBaseRevision(null); setDraftDetached(false); setMessage(null); }}>
        <option value="">{t("sharedPlans.selectSavedPlan")}</option>
        {catalog.map((plan) => <option key={plan.plan_key} value={plan.plan_key}>{plan.plan_key}</option>)}
      </select></label>
      <button className="button button--secondary" type="button" onClick={createNew}>{t("plans.new")}</button>
    </div>

    {templateCatalog.length > 0 ? <details className="advanced-disclosure">
      <summary>{t("sharedPlans.createFromTemplate")}</summary>
      <p className="muted">{t("sharedPlans.templatesHelp")}</p>
      <div className="template-plan-choice__controls">
        <label><span>{t("sharedPlans.template")}</span><select value={templateKey} onChange={(event) => { const nextKey = event.target.value; const nextTemplate = templateCatalog.find((template) => template.template_key === nextKey); setTemplateKey(nextKey); setTemplateSha(nextTemplate?.sha256 ?? null); setMessage(null); }}>
          <option value="">{t("sharedPlans.selectTemplate")}</option>
          {templateCatalog.map((template) => <option key={`${template.template_key}:${template.sha256}`} value={template.template_key}>{template.template_key}</option>)}
        </select></label>
        <button className="button button--secondary" type="button" disabled={actionsDisabled || !templateAuthorityFresh || !selectedTemplateStable || !canCreateTemplate} onClick={() => void createFromTemplate()}>{t("sharedPlans.useTemplate")}</button>
      </div>
    </details> : null}

    {(planKey || draft) ? <div className="plan-editor-column">
      <div className="form-grid">
        <label><span>{t("sharedPlans.key")}</span><input value={draftKey} onChange={(event) => setDraftKey(event.target.value)} placeholder="python-quality" /></label>
      </div>
      <label><span>{t("sharedPlans.content")}</span><textarea className="plan-yaml-editor" rows={18} spellCheck={false} value={draft} onChange={(event) => setDraft(event.target.value)} /></label>
      {draftDetached ? <p className="notice notice--warning" role="status">{t("sharedPlans.editorServerRemoved")}</p> : null}
      {editorStale ? <div className="notice notice--warning" role="status"><p>{t("sharedPlans.editorServerChanged")}</p><button className="button button--secondary button--small" type="button" disabled={selectedPlan.resource.status !== "read-fresh" || selectedPlan.resource.data === null} onClick={loadLatestServerPlan}>{t("sharedPlans.loadLatestServerVersion")}</button></div> : null}
      <div className="action-row"><button className="button button--primary" type="button" disabled={actionsDisabled || !canSaveDraft || !draftKey.trim() || !draft.trim()} onClick={() => void save()}>{t("sharedPlans.save")}</button></div>
    </div> : null}

    {message && sameIdentityIncarnation(message.owner, editorOwnerRef.current) ? <p className="success-text" role="status" aria-live="polite">{message.text}</p> : null}
  </section>;
}

function validateSavedPlanIdentity(
  result: WriteCommandResult<SharedPlanDocument>,
  expectedPlanKey: string,
): WriteCommandResult<SharedPlanDocument> {
  if (result.outcome !== "saved" || result.value.plan_key === expectedPlanKey) return result;
  const diagnostic = appDiagnosticStore.record({
    source: "shared-plan-save-identity",
    error: new Error(`Shared Plan mutation identity mismatch: expected ${expectedPlanKey}, received ${result.value.plan_key}`),
    scope: "plans",
    causalityRevision: result.causalityRevision,
  });
  return { outcome: "write-error", diagnosticId: diagnostic.id, recovery: "refresh" };
}
