import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient, useMutationBusy } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorBanner } from "@/components/common/ErrorBanner";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";
import { GameTapeStepArtifacts, RunArtifactExportForm } from "@/features/game-tape/GameTapeFeature";
import { CurrentRunPanel } from "@/features/maintenance/MaintenanceFeature";
import type { CatalogCandidate, CatalogExploration, CatalogPreview, CatalogRunRequest, FleetRepository, FleetRunDetail, FleetRunState, IncludedFleetRepository } from "@generated/models";

interface Props {
  repositories: readonly FleetRepository[];
  fleetFresh: boolean;
  onOpenRun: (runId: string, repositoryId: string) => void;
  onOpenRepositories: () => void;
  runStatus: {
    activeRun: FleetRunState | null;
    activeRunDetail: FleetRunDetail | null;
    completedLocalRun: FleetRunState | null;
    completedLocalRunDetail: FleetRunDetail | null;
  };
  onGenerate: (repositoryId: string, request: Omit<CatalogRunRequest, "fleet_run_id">) => Promise<WriteCommandResult<FleetRunState>>;
  onCancel: (runId: string) => void;
}

export function CatalogFeature({ repositories, fleetFresh, onOpenRun, onOpenRepositories, runStatus, onGenerate, onCancel }: Props) {
  const { t } = useTranslation();
  const busy = useMutationBusy();
  const [repositoryId, setRepositoryId] = useState("");
  const included = repositories.filter((repository): repository is IncludedFleetRepository => repository.scope === "INCLUDED");
  const selected = included.find((entry) => entry.repository.repository_id === repositoryId);
  return <div className="catalog-workspace">
    <ol className="catalog-stages" aria-label={t("catalog.journey")}>
      {["explore", "review", "generate", "inspect", "export"].map((stage, index) => <li key={stage}><span aria-hidden="true">{index + 1}</span>{t(`catalog.stages.${stage}`)}</li>)}
    </ol>
    <section className="panel catalog-selection">
      <label className="field"><span>{t("catalog.repository")}</span><select value={selected ? repositoryId : ""} disabled={busy || !fleetFresh} onChange={(event) => setRepositoryId(event.target.value)}>
        <option value="">{t("catalog.chooseRepository")}</option>
        {included.map((entry) => <option key={entry.repository.repository_id} value={entry.repository.repository_id}>{entry.repository.name}</option>)}
      </select></label>
      <p className="muted">{t("catalog.localMode")}</p>
    </section>
    {included.length === 0 && fleetFresh ? <EmptyState title={t("catalog.noRepositories")} message={t("catalog.noRepositoriesHelp")} action={<button className="button button--primary" onClick={onOpenRepositories}>{t("catalog.openRepositories")}</button>} /> : null}
    {selected ? <CatalogSession key={`${selected.repository.repository_id}:${selected.repository_instance_id}`} repository={selected} fleetFresh={fleetFresh} onOpenRun={onOpenRun} runStatus={runStatus} onGenerate={onGenerate} onCancel={onCancel} /> : null}
  </div>;
}

interface Draft {
  name: string;
  description: string;
  owner: string;
  type: string;
  lifecycle: string;
  languages: string;
}

const EMPTY_DRAFT: Draft = { name: "", description: "", owner: "", type: "", lifecycle: "", languages: "" };

function CatalogSession({ repository, fleetFresh, onOpenRun, runStatus, onGenerate, onCancel }: Pick<Props, "fleetFresh" | "onOpenRun" | "runStatus" | "onGenerate" | "onCancel"> & { repository: IncludedFleetRepository }) {
  const { t } = useTranslation();
  const client = useApiClient();
  const busy = useMutationBusy();
  const [query, setQuery] = useState("");
  const [path, setPath] = useState("");
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [approval, setApproval] = useState<CatalogPreview | null>(null);
  const [submittedRun, setSubmittedRun] = useState<FleetRunState | null>(null);
  const [submissionFailed, setSubmissionFailed] = useState(false);
  const [artifactKey, setArtifactKey] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const repositoryId = repository.repository.repository_id;
  const explorationIdentity = canonicalIdentity({ repositoryId, instance: repository.repository_instance_id, query, path });
  const requestExplore = useCallback((signal: AbortSignal) => client.catalogExplore(repositoryId, {
    expected_repository_instance_id: repository.repository_instance_id,
    query,
    ...(path.trim() ? { path: path.trim() } : {}),
  }, { signal }), [client, path, query, repository.repository_instance_id, repositoryId]);
  const explorationRead = useReadResource<CatalogExploration>({
    scope: "catalog-exploration",
    source: "catalog exploration",
    identity: explorationIdentity,
    auto: false,
    enabled: query.trim().length > 0,
    errorMessage: t("catalog.exploreFailed"),
    request: requestExplore,
    retainDataOnError: () => false,
    validate: (value) => {
      if (value.repository.repository_id !== repositoryId || value.repository_instance_id !== repository.repository_instance_id) throw new Error("Catalog evidence belongs to another repository selection");
    },
  });
  const exploration = explorationRead.resource.status === "read-fresh" ? explorationRead.resource.data : null;
  const candidate = useMemo<CatalogCandidate | null>(() => {
    const type = exploration?.candidate_options.component_types.find((value) => value === draft.type);
    const lifecycle = exploration?.candidate_options.lifecycles.find((value) => value === draft.lifecycle);
    if (type === undefined || lifecycle === undefined) return null;
    return { ...draft, type, lifecycle, languages: draft.languages.split(",").map((value) => value.trim()).filter((value) => value !== "") };
  }, [draft, exploration]);
  const previewIdentity = canonicalIdentity({ exploration: exploration?.source_authority ?? null, candidate });
  const requestPreview = useCallback((signal: AbortSignal) => {
    if (!exploration || !candidate) return Promise.reject(new Error("Explore the selected source and select candidate options before preview"));
    return client.catalogPreview(repositoryId, { source_authority: exploration.source_authority, candidate }, { signal });
  }, [candidate, client, exploration, repositoryId]);
  const previewRead = useReadResource<CatalogPreview>({
    scope: "catalog-preview",
    source: "catalog preview",
    identity: previewIdentity,
    auto: false,
    enabled: exploration !== null && candidate !== null,
    errorMessage: t("catalog.previewFailed"),
    request: requestPreview,
    retainDataOnError: () => false,
    validate: (value) => {
      if (value.repository.repository_id !== repositoryId || value.source_authority.authority_id !== exploration?.source_authority.authority_id || canonicalIdentity(value.candidate) !== canonicalIdentity(candidate)) throw new Error("Catalog preview does not match the reviewed source and candidate");
    },
  });
  const preview = previewRead.resource.status === "read-fresh" ? previewRead.resource.data : null;
  const approved = preview !== null && approval === preview;
  const ownedActive = runStatus.activeRun?.fleet_run_id === submittedRun?.fleet_run_id ? runStatus.activeRun : null;
  const ownedCompleted = runStatus.completedLocalRun?.fleet_run_id === submittedRun?.fleet_run_id ? runStatus.completedLocalRun : null;
  const run = ownedActive ?? ownedCompleted ?? submittedRun;
  const observedDetail = ownedActive ? runStatus.activeRunDetail : ownedCompleted ? runStatus.completedLocalRunDetail : null;
  const detail = observedDetail?.run.fleet_run_id === run?.fleet_run_id ? observedDetail : null;
  const recordedResult = detail?.game_tape.status ?? null;
  const passed = run?.status === "COMPLETED" && recordedResult === "PASSED";
  const unsuccessful = run?.status === "FAILED" || run?.status === "CANCELLED" || (run?.status === "COMPLETED" && recordedResult !== null && recordedResult !== "PASSED");
  const recordedRepository = detail?.game_tape.repositories.find((entry) => entry.repository.repository_id === repositoryId);
  const artifacts = (recordedRepository?.steps ?? []).flatMap((step) => step.status === "PASSED" ? (step.artifacts ?? []).filter((artifact) => artifact.status === "PRESENT" && artifact.retained_object !== null && artifact.retained_object !== undefined).map((artifact) => ({ step, artifact })) : []);
  const selectedArtifact = artifacts.find(({ step, artifact }) => `${step.step_execution_id}:${artifact.path}` === artifactKey) ?? (artifacts.length === 1 ? artifacts[0] : undefined);
  const working = busy || submitting;
  const exploring = explorationRead.resource.status === "read-loading" || explorationRead.resource.status === "read-refreshing";
  const previewing = previewRead.resource.status === "read-loading" || previewRead.resource.status === "read-refreshing";

  const revoke = () => {
    setApproval(null);
    previewRead.clear();
    setSubmittedRun(null);
    setSubmissionFailed(false);
    setArtifactKey("");
  };
  const explore = () => {
    revoke();
    explorationRead.clear();
    void explorationRead.refresh();
  };
  const updateDraft = (field: keyof Draft, value: string) => { revoke(); setDraft((current) => ({ ...current, [field]: value })); };
  const generate = async () => {
    if (!preview || !approved || working || runStatus.activeRun !== null) return;
    const selectedPreview = preview;
    setApproval(null);
    setSubmissionFailed(false);
    setSubmitting(true);
    try {
      const result = await onGenerate(repositoryId, {
        source_authority: selectedPreview.source_authority,
        candidate: selectedPreview.candidate,
        expected_plan_id: selectedPreview.plan_id,
        expected_plan_sha256: selectedPreview.plan_sha256,
        expected_execution_digest: selectedPreview.execution_digest,
      });
      if (!mounted.current) return;
      if (result.outcome === "saved") setSubmittedRun(result.value);
      else { setSubmissionFailed(true); previewRead.clear(); explorationRead.clear(); }
    } finally { if (mounted.current) setSubmitting(false); }
  };

  return <>
    <section className="panel catalog-explore" aria-busy={exploring}>
      <div className="section-heading"><div><span className="eyebrow">{t("catalog.stages.explore")}</span><h2>{t("catalog.exploreTitle")}</h2><p>{t("catalog.selectedTarget", { name: repository.repository.name })}</p></div></div>
      <div className="form-grid">
        <label><span>{t("catalog.search")}</span><input value={query} disabled={working} onChange={(event) => { revoke(); explorationRead.clear(); setQuery(event.target.value); }} placeholder={t("catalog.searchPlaceholder")} /></label>
        <label><span>{t("catalog.path")}</span><input value={path} disabled={working} onChange={(event) => { revoke(); explorationRead.clear(); setPath(event.target.value); }} placeholder={t("catalog.pathPlaceholder")} /></label>
      </div>
      <button className="button button--primary" type="button" disabled={working || exploring || !fleetFresh || !query.trim()} onClick={explore}>{exploring ? t("catalog.exploring") : t("catalog.exploreButton")}</button>
      {explorationRead.resource.status === "read-error" ? <ErrorBanner message={explorationRead.resource.failure?.message ?? t("catalog.exploreFailed")} /> : null}
      {exploration ? <ExplorationEvidence exploration={exploration} /> : <p className="muted">{t("catalog.exploreHelp")}</p>}
    </section>
    {exploration ? <section className="panel catalog-review" aria-busy={previewing}>
      <div className="section-heading"><div><span className="eyebrow">{t("catalog.stages.review")}</span><h2>{t("catalog.reviewTitle")}</h2><p>{t("catalog.reviewHelp")}</p></div></div>
      <div className="form-grid">{(Object.keys(EMPTY_DRAFT) as (keyof Draft)[]).map((field) => <label key={field}><span>{t(`catalog.fields.${field}`)}</span>{field === "description" ? <textarea rows={3} value={draft[field]} disabled={working} onChange={(event) => updateDraft(field, event.target.value)} /> : field === "type" || field === "lifecycle" ? <select value={draft[field]} disabled={working} onChange={(event) => updateDraft(field, event.target.value)}><option value="">{t("catalog.chooseValue")}</option>{(field === "type" ? exploration.candidate_options.component_types : exploration.candidate_options.lifecycles).map((value) => <option key={value} value={value}>{value}</option>)}</select> : <input value={draft[field]} placeholder={field === "owner" ? t("catalog.ownerPlaceholder") : undefined} disabled={working} onChange={(event) => updateDraft(field, event.target.value)} />}</label>)}</div>
      <button className="button button--secondary" type="button" disabled={working || previewing || !fleetFresh || candidate === null} onClick={() => { setApproval(null); previewRead.clear(); void previewRead.refresh(); }}>{previewing ? t("catalog.previewing") : t("catalog.previewButton")}</button>
      {previewRead.resource.status === "read-error" ? <ErrorBanner message={previewRead.resource.failure?.message ?? t("catalog.previewFailed")} /> : null}
      {preview ? <div className="catalog-preview">
        <h3>{t("catalog.previewTitle")}</h3>
        <dl className="catalog-facts">{(Object.keys(EMPTY_DRAFT) as (keyof Draft)[]).map((field) => <div key={field}><dt>{t(`catalog.fields.${field}`)}</dt><dd>{Array.isArray(preview.candidate[field]) ? preview.candidate.languages.join(", ") : preview.candidate[field]}</dd></div>)}</dl>
        <pre className="catalog-yaml" aria-label={t("catalog.yamlLabel")}>{preview.yaml}</pre>
        <label className="checkbox-inline catalog-approval"><input type="checkbox" checked={approved} disabled={working || !fleetFresh} onChange={(event) => setApproval(event.target.checked ? preview : null)} /><span>{t("catalog.approval")}</span></label>
        <button className="button button--primary" type="button" disabled={!approved || working || !fleetFresh || runStatus.activeRun !== null} onClick={() => void generate()}>{submitting ? t("catalog.generating") : t("catalog.generateButton")}</button>
        {runStatus.activeRun !== null ? <p className="muted" role="status">{t("catalog.activeRunBlocksGeneration")}</p> : null}
      </div> : null}
    </section> : null}
    {submissionFailed ? <ErrorBanner message={t("catalog.runRefused")} /> : null}
    {run ? <section className="catalog-original-run" aria-label={t("catalog.originalRun")}>
      <div className="notice notice--scope"><strong>{t("catalog.originalRun")}</strong><p><code>{run.fleet_run_id}</code></p><dl className="catalog-facts"><div><dt>{t("catalog.runLifecycle")}</dt><dd>{run.status}</dd></div><div><dt>{t("catalog.recordedResult")}</dt><dd>{recordedResult ?? t("catalog.waitingForEvidence")}</dd></div></dl><button className="button button--secondary" type="button" onClick={() => onOpenRun(run.fleet_run_id, repositoryId)}>{t("catalog.openTape")}</button></div>
      {ownedActive ? <CurrentRunPanel run={ownedActive} detail={detail} mode="active" onCancel={onCancel} onInspect={() => onOpenRun(ownedActive.fleet_run_id, repositoryId)} /> : null}
      {unsuccessful ? <p className="notice notice--warning" role="status">{t("catalog.runNotSuccessful")}</p> : null}
      {passed ? <section className="panel catalog-export">
        <h2>{t("catalog.exportTitle")}</h2>
        {recordedRepository ? (recordedRepository.steps ?? []).map((step) => <GameTapeStepArtifacts key={step.step_execution_id} artifacts={step.artifacts ?? []} ownerIdentity={step.step_execution_id} artifactOwner={{ repository_id: repositoryId, repository_execution_id: recordedRepository.repository_execution_id, step_id: step.step_id, step_execution_id: step.step_execution_id }} />) : <p role="status">{t("catalog.waitingForEvidence")}</p>}
        {artifacts.length > 1 ? <label className="field"><span>{t("catalog.artifact")}</span><select value={artifactKey} onChange={(event) => setArtifactKey(event.target.value)} disabled={working}><option value="">{t("catalog.chooseArtifact")}</option>{artifacts.map(({ step, artifact }) => <option key={`${step.step_execution_id}:${artifact.path}`} value={`${step.step_execution_id}:${artifact.path}`}>{step.step_id} · {artifact.path}</option>)}</select></label> : null}
        {selectedArtifact && selectedArtifact.artifact.status === "PRESENT" ? <RunArtifactExportForm key={`${run.fleet_run_id}:${selectedArtifact.step.step_execution_id}:${selectedArtifact.artifact.path}:${selectedArtifact.artifact.retained_object?.reference_id}`} fleetRunId={run.fleet_run_id} repositoryId={repositoryId} stepId={selectedArtifact.step.step_id} artifact={selectedArtifact.artifact} /> : null}
      </section> : null}
    </section> : null}
  </>;
}

function ExplorationEvidence({ exploration }: { exploration: CatalogExploration }) {
  const { t } = useTranslation();
  const qualification = exploration.qualification;
  const relationships = exploration.relationship_qualification;
  return <div className="catalog-evidence">
    <div className="catalog-evidence-heading"><h3>{t("catalog.hashmarks")}</h3><span className="mode-pill">{t("catalog.noProvider")}</span></div>
    <p className="notice notice--scope">{t("catalog.evidenceLimits")}</p>
    <dl className="catalog-facts">
      <div><dt>{t("catalog.generation")}</dt><dd>{qualification.generation ?? t("catalog.unknown")}</dd></div>
      <div><dt>{t("catalog.identityGeneration")}</dt><dd>{qualification.identity_generation ?? t("catalog.unknown")}</dd></div>
      <div><dt>{t("catalog.freshness")}</dt><dd>{qualification.freshness}</dd></div>
      <div><dt>{t("catalog.observationCompleteness")}</dt><dd>{qualification.completeness}</dd></div>
      <div><dt>{t("catalog.repositoryCoverage")}</dt><dd>{qualification.repository_wide_completeness}</dd></div>
      <div><dt>{t("catalog.truncation")}</dt><dd>{qualification.truncated ? t("catalog.truncated") : t("catalog.notTruncated")}</dd></div>
      <div><dt>{t("catalog.negativeEvidence")}</dt><dd>{qualification.negative_evidence}</dd></div>
    </dl>
    {qualification.admissibility_reasons.length > 0 ? <ul className="compact-list">{qualification.admissibility_reasons.map((reason) => <li key={reason}><code>{reason}</code></li>)}</ul> : null}
    <h4>{t("catalog.languages")}</h4><ul className="catalog-language-list">{exploration.languages.map((language) => <li key={language.name}><strong>{language.name}</strong> · {language.count}</li>)}</ul>
    <h4>{t("catalog.hits")}</h4>{exploration.hits.length === 0 ? <p role="status">{t("catalog.noHits")}</p> : <ol className="catalog-hit-list">{exploration.hits.map((hit) => <li key={`${hit.path}:${hit.first_line}:${hit.qualname}`}><strong>{hit.qualname || hit.name || hit.path}</strong><code>{typeof hit.first_line === "number" && typeof hit.last_line === "number" ? `${hit.path}:${hit.first_line}–${hit.last_line}` : `${hit.path} · ${t("catalog.lineUnavailable")}`}</code>{hit.signature ? <code>{hit.signature}</code> : null}</li>)}</ol>}
    <h4>{t("catalog.relations")}</h4><p>{exploration.relationship_message} <small>{exploration.relationship_status}</small></p>
    {relationships ? <div className="catalog-relation-qualifiers">
      <dl className="catalog-facts">
        <div><dt>{t("catalog.relationshipDepth")}</dt><dd>{relationships.max_depth ?? t("catalog.unknown")}</dd></div>
        <div><dt>{t("catalog.relationshipCallLimit")}</dt><dd>{relationships.call_limit_per_symbol ?? t("catalog.unknown")}</dd></div>
        <div><dt>{t("catalog.relationshipReferenceLimit")}</dt><dd>{relationships.ref_limit_per_symbol ?? t("catalog.unknown")}</dd></div>
        <div><dt>{t("catalog.lifecycleCompleteness")}</dt><dd>{relationships.lifecycle_completeness ?? t("catalog.unknown")}</dd></div>
        <div><dt>{t("catalog.lifecycleLimits")}</dt><dd>{relationships.lifecycle_truncated === null || relationships.lifecycle_truncated === undefined ? t("catalog.unknown") : relationships.lifecycle_truncated ? t("catalog.truncated") : t("catalog.notTruncated")}</dd></div>
      </dl>
      {(relationships.unresolved_reasons ?? []).length ? <div><h5>{t("catalog.unresolvedRelationships")}</h5><ul className="compact-list">{(relationships.unresolved_reasons ?? []).map((reason, index) => <li key={`${reason}:${index}`}><code>{reason}</code></li>)}</ul></div> : null}
      {(relationships.external_or_unindexed_reasons ?? []).length ? <div><h5>{t("catalog.externalRelationships")}</h5><ul className="compact-list">{(relationships.external_or_unindexed_reasons ?? []).map((reason, index) => <li key={`${reason}:${index}`}><code>{reason}</code></li>)}</ul></div> : null}
    </div> : null}
    {exploration.relations.length === 0 ? null : <ul className="compact-list">{exploration.relations.map((relation, index) => <li key={`${relation.source_path}:${relation.target}:${index}`}><code>{relation.source_path}</code> → <code>{relation.target}</code><small>{relation.kind} · {relation.confidence}{(relation.resolved_paths ?? []).length ? ` · ${(relation.resolved_paths ?? []).join(", ")}` : ""}</small></li>)}</ul>}
    <section className="catalog-git"><h3>{t("catalog.gitAuthor")}</h3><p>{exploration.git_author.message}</p>{exploration.git_author.status === "available" ? <dl className="catalog-facts"><div><dt>{t("catalog.author")}</dt><dd>{exploration.git_author.author}</dd></div><div><dt>{t("catalog.commit")}</dt><dd><code>{exploration.git_author.commit}</code></dd></div><div><dt>{t("catalog.date")}</dt><dd>{exploration.git_author.date}</dd></div><div><dt>{t("catalog.path")}</dt><dd><code>{exploration.git_author.path}</code></dd></div></dl> : null}<p className="muted">{t("catalog.authorIsNotOwner")}</p></section>
    <section className="notice notice--scope"><h3>{t("catalog.humanOwner")}</h3><p>{t("catalog.manualOwner")}</p></section>
    <details className="advanced-disclosure"><summary>{t("catalog.provenance")}</summary><dl className="catalog-facts"><div><dt>{t("catalog.sourceIdentity")}</dt><dd><code>{exploration.source_authority.authority_id}</code></dd></div><div><dt>{t("catalog.producer")}</dt><dd><code>{exploration.producer_identity}</code></dd></div></dl></details>
  </div>;
}
