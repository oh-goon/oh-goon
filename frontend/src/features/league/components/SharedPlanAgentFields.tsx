import { useTranslation } from "react-i18next";

import type { RunAgentSelection, RunRepairSelection } from "@generated/models";

export interface SharedPlanAgentDraft {
  enabled: boolean;
  implementation: "" | "pi" | "opencode" | "codex";
  profile: string;
  modelProfile: string;
  acceptance: string;
  repositoryRoot: string;
  skillRoot: string;
  repair: boolean;
  reviewPasses: string;
  writeRoots: string;
  timeoutSeconds: string;
  stepTimeoutSeconds: string;
}

export const EMPTY_SHARED_PLAN_AGENT: SharedPlanAgentDraft = {
  enabled: false, implementation: "", profile: "", modelProfile: "", acceptance: "",
  repositoryRoot: "", skillRoot: "", repair: false, reviewPasses: "2", writeRoots: "",
  timeoutSeconds: "600", stepTimeoutSeconds: "120",
};

export function sharedPlanAgentSelection(draft: SharedPlanAgentDraft): RunAgentSelection | null {
  return draft.enabled ? {
    implementation: draft.implementation || null,
    profile: draft.implementation ? draft.profile : null,
    model_profile: draft.implementation ? null : draft.modelProfile || null,
    acceptance: draft.acceptance, repository_root: draft.repositoryRoot, skill_root: draft.skillRoot,
  } : null;
}

export function sharedPlanRepairSelection(draft: SharedPlanAgentDraft): RunRepairSelection | null {
  return draft.enabled && draft.repair ? {
    review_passes: Number(draft.reviewPasses),
    write_roots: draft.writeRoots.split("\n").filter(Boolean),
    timeout_seconds: Number(draft.timeoutSeconds), step_timeout_seconds: Number(draft.stepTimeoutSeconds),
  } : null;
}

export function SharedPlanAgentFields({ draft, onChange }: {
  draft: SharedPlanAgentDraft;
  onChange: (draft: SharedPlanAgentDraft) => void;
}) {
  const { t } = useTranslation();
  const edit = (name: keyof SharedPlanAgentDraft, value: string | boolean) => onChange({ ...draft, [name]: value });
  return <details className="advanced-disclosure">
    <summary>{t("sharedPlans.agent.title")}</summary>
    <p>{t("sharedPlans.agent.help")}</p>
    <label className="checkbox-row"><input type="checkbox" checked={draft.enabled} onChange={(event) => edit("enabled", event.target.checked)} />{t("sharedPlans.agent.enabled")}</label>
    {draft.enabled ? <>
      <div className="form-grid">
        <label><span>{t("sharedPlans.agent.implementation")}</span><select value={draft.implementation} onChange={(event) => edit("implementation", event.target.value)}>
          <option value="">{t("sharedPlans.agent.providerModel")}</option><option value="pi">pi</option><option value="opencode">opencode</option><option value="codex">codex</option>
        </select></label>
        {draft.implementation ? <label><span>{t("sharedPlans.agent.profile")}</span><input required value={draft.profile} onChange={(event) => edit("profile", event.target.value)} /></label>
          : <label><span>{t("sharedPlans.agent.modelProfile")}</span><input value={draft.modelProfile} onChange={(event) => edit("modelProfile", event.target.value)} /></label>}
        <label><span>{t("sharedPlans.agent.acceptance")}</span><input required value={draft.acceptance} onChange={(event) => edit("acceptance", event.target.value)} /></label>
        <label><span>{t("sharedPlans.agent.repositoryRoot")}</span><input required value={draft.repositoryRoot} onChange={(event) => edit("repositoryRoot", event.target.value)} /></label>
        <label><span>{t("sharedPlans.agent.skillRoot")}</span><input required value={draft.skillRoot} onChange={(event) => edit("skillRoot", event.target.value)} /></label>
      </div>
      <label className="checkbox-row"><input type="checkbox" checked={draft.repair} onChange={(event) => edit("repair", event.target.checked)} />{t("sharedPlans.agent.repair")}</label>
      {draft.repair ? <div className="form-grid">
        <label><span>{t("sharedPlans.agent.reviewPasses")}</span><input type="number" value={draft.reviewPasses} onChange={(event) => edit("reviewPasses", event.target.value)} /></label>
        <label><span>{t("sharedPlans.agent.writeRoots")}</span><textarea value={draft.writeRoots} onChange={(event) => edit("writeRoots", event.target.value)} /></label>
        <label><span>{t("sharedPlans.agent.timeout")}</span><input type="number" value={draft.timeoutSeconds} onChange={(event) => edit("timeoutSeconds", event.target.value)} /></label>
        <label><span>{t("sharedPlans.agent.stepTimeout")}</span><input type="number" value={draft.stepTimeoutSeconds} onChange={(event) => edit("stepTimeoutSeconds", event.target.value)} /></label>
      </div> : null}
    </> : null}
  </details>;
}
