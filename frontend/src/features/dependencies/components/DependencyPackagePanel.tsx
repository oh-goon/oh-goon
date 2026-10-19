import { useState, type ChangeEvent, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { StatusBadge } from "@/components/common/StatusBadge";
import type { ReadResource } from "@/domain/reads/ReadResource";
import type { DependencyCoverage, DependencyEvidence } from "@/features/dependencies/model";
import type {
  DependencyAdvisoryOverview,
  DependencyCoverageRepository,
  DependencyCoverageRepositoryPage,
  DependencyPackageOverview,
  DependencyRepositoryMatch,
  DependencyRepositoryPage,
} from "@generated/models";

interface FilterState {
  resolvedVersion: string;
  versionSpecifier: string;
  advisoryId: string;
  evidence: DependencyEvidence;
  coverage: DependencyCoverage;
  offset: number;
}

interface Props extends FilterState {
  packageName: string;
  overview: ReadResource<DependencyPackageOverview>;
  advisory: ReadResource<DependencyAdvisoryOverview>;
  repositories: ReadResource<DependencyRepositoryPage>;
  coverageRepositories: ReadResource<DependencyCoverageRepositoryPage>;
  selecting: boolean;
  onBack: () => void;
  onFilter: (next: Partial<FilterState>) => void;
  onMaterialize: () => void;
  onOpenEvidence: (runId: string, stepId: string) => void;
  onOpenRepository: (repositoryId: string) => void;
}

function evidenceTone(state: string | undefined): "success" | "warning" | "neutral" {
  return state === "current" ? "success" : state === "unknown" ? "warning" : "neutral";
}

const resetMatchFilters = {
  resolvedVersion: "",
  versionSpecifier: "",
  advisoryId: "",
  evidence: "all" as const,
  offset: 0,
};

export function DependencyPackagePanel({
  packageName, resolvedVersion, versionSpecifier, advisoryId, evidence, coverage, offset,
  overview, advisory, repositories, coverageRepositories, selecting,
  onBack, onFilter, onMaterialize, onOpenEvidence, onOpenRepository,
}: Props) {
  const { t } = useTranslation();
  const data = overview.data;
  const advisoryData = advisory.data;
  const [advisoryDraft, setAdvisoryDraft] = useState(advisoryId);
  const [specifierDraft, setSpecifierDraft] = useState(versionSpecifier);
  const matching = coverage === "matching";
  const matchPage = matching ? repositories.data : null;
  const coveragePage = matching ? null : coverageRepositories.data;
  const matchItems = matchPage?.items ?? [];
  const coverageItems = coveragePage?.items ?? [];
  const pageTotal = matchPage?.total ?? coveragePage?.total ?? 0;
  const pageLimit = matchPage?.limit ?? coveragePage?.limit ?? 25;
  const pageOffset = matchPage?.offset ?? coveragePage?.offset ?? 0;
  const pageLength = matching ? matchItems.length : coverageItems.length;
  const loading = overview.status === "read-loading"
    || (matching ? repositories.status === "read-loading" : coverageRepositories.status === "read-loading");
  const active = coverage !== "matching" || Boolean(resolvedVersion || versionSpecifier || evidence !== "all");

  const chooseCoverage = (nextCoverage: DependencyCoverage, nextEvidence: DependencyEvidence = "all") => {
    onFilter({ ...resetMatchFilters, coverage: nextCoverage, evidence: nextEvidence });
  };

  return <section className="dependency-package-workspace">
    <button className="text-button dependency-back" type="button" onClick={onBack}>← {t("dependencies.backToSearch")}</button>
    <section className="panel dependency-package-panel" aria-busy={loading}>
      <div className="section-heading dependency-package-heading"><div>
        <span className="eyebrow">{t("dependencies.packageEyebrow")}</span>
        <h2>{packageName}</h2>
        <p>{t("dependencies.packageDescription")}</p>
      </div>{data ? <span className="dependency-package-total"><strong>{data.observed_repositories}</strong><small>{t("dependencies.observedRepositories")}</small></span> : null}</div>
      {overview.status === "read-error" ? <p className="notice notice--warning" role="alert">{overview.failure?.message}</p> : null}
      {data ? <>
        <div className="dependency-coverage-grid" role="group" aria-label={t("dependencies.coverageTitle")}>
          <CoverageFact active={matching && evidence === "current"} value={data.freshness.current} label={t("dependencies.currentEvidence")} onClick={() => chooseCoverage("matching", "current")} />
          <CoverageFact active={coverage === "absent_from_lock"} value={data.freshness.no_matching_dependency} label={t("dependencies.provenNoMatch")} onClick={() => chooseCoverage("absent_from_lock")} />
          <CoverageFact active={matching && evidence === "unknown"} value={data.freshness.unknown} label={t("dependencies.unknownEvidence")} onClick={() => chooseCoverage("matching", "unknown")} />
          <CoverageFact active={matching && evidence === "stale"} value={data.freshness.stale} label={t("dependencies.staleEvidence")} onClick={() => chooseCoverage("matching", "stale")} />
          <CoverageFact active={coverage === "no_current_evidence"} value={data.freshness.no_current_evidence} label={t("dependencies.noCurrentEvidence")} onClick={() => chooseCoverage("no_current_evidence")} />
        </div>
        <p className="dependency-coverage-help">{t("dependencies.coverageHelp")}</p>
      </> : null}

      {matching ? <>
        {data ? <section className="dependency-facet-section">
          <h3>{t("dependencies.versions")}</h3>
          <div className="dependency-version-grid">
            <button type="button" className={!resolvedVersion ? "dependency-version dependency-version--active" : "dependency-version"} onClick={() => onFilter({ resolvedVersion: "", versionSpecifier: "", advisoryId: "", coverage: "matching", offset: 0 })}><strong>{data.observed_repositories}</strong><span>{t("dependencies.allVersions")}</span></button>
            {(data.versions ?? []).map((item) => <button type="button" className={resolvedVersion === item.version ? "dependency-version dependency-version--active" : "dependency-version"} key={item.version} onClick={() => onFilter({ resolvedVersion: item.version, versionSpecifier: "", advisoryId: "", coverage: "matching", offset: 0 })}><strong>{item.repositories}</strong><span>{item.version}</span></button>)}
          </div>
        </section> : null}
        <section className="dependency-facet-section dependency-advisory-section">
          <div className="section-heading"><div><h3>{t("dependencies.advisoryTitle")}</h3><p>{t("dependencies.advisoryDescription")}</p></div></div>
          <form className="dependency-advisory-form" onSubmit={(event: FormEvent<HTMLFormElement>) => {
            event.preventDefault();
            onFilter({ advisoryId: advisoryDraft.trim(), versionSpecifier: specifierDraft.trim(), resolvedVersion: "", coverage: "matching", offset: 0 });
          }}>
            <label><span>{t("dependencies.advisoryId")}</span><input value={advisoryDraft} onChange={(event: ChangeEvent<HTMLInputElement>) => setAdvisoryDraft(event.target.value)} placeholder="CVE-2026-XXXX" /></label>
            <label><span>{t("dependencies.affectedRange")}</span><input value={specifierDraft} onChange={(event: ChangeEvent<HTMLInputElement>) => setSpecifierDraft(event.target.value)} placeholder=">=2.0,<2.18.0" /></label>
            <button className="button button--secondary" type="submit" disabled={!advisoryDraft.trim() || !specifierDraft.trim()}>{t("dependencies.applyAffectedRange")}</button>
          </form>
          {advisory.status === "read-error" ? <p className="notice notice--warning" role="alert">{advisory.failure?.message}</p> : null}
          {advisoryData ? <div className="dependency-coverage-grid dependency-advisory-summary">
            <CoverageFact value={advisoryData.affected_repositories} label={t("dependencies.affectedRepositories")} onClick={() => onFilter({ coverage: "matching", evidence: "current", offset: 0 })} />
            <CoverageFact value={advisoryData.observed_not_affected} label={t("dependencies.observedNotAffected")} />
            <CoverageFact value={advisoryData.version_unclassified} label={t("dependencies.versionUnclassified")} />
            <CoverageFact value={advisoryData.no_current_evidence} label={t("dependencies.noCurrentEvidence")} onClick={() => chooseCoverage("no_current_evidence")} />
          </div> : null}
          {versionSpecifier ? <p className="dependency-coverage-help">{t("dependencies.rangeIntegrityHelp")}</p> : null}
        </section>
        <section className="dependency-facet-section">
          <div className="dependency-filter-row"><div><strong>{t("dependencies.evidence")}</strong><div className="dependency-filter-chips">{(["all", "current", "unknown", "stale"] as const).map((value) => <button type="button" className={evidence === value ? "filter-chip filter-chip--active" : "filter-chip"} key={value} onClick={() => onFilter({ evidence: value, coverage: "matching", offset: 0 })}>{t(`dependencies.evidenceStates.${value}`)}</button>)}</div></div></div>
        </section>
      </> : <div className="notice notice--scope"><strong>{t(`dependencies.coverageStates.${coverage}`)}</strong><p>{t(`dependencies.coverageDescriptions.${coverage}`)}</p></div>}

      {active ? <div className="dependency-active-filters" role="group" aria-label={t("dependencies.activeFilters")}><span>{t("dependencies.activeFilters")}</span>
        {coverage !== "matching" ? <button type="button" className="filter-chip filter-chip--active" onClick={() => chooseCoverage("matching")}>{t(`dependencies.coverageStates.${coverage}`)} ×</button> : null}
        {resolvedVersion ? <button type="button" className="filter-chip filter-chip--active" onClick={() => onFilter({ resolvedVersion: "", versionSpecifier: "", advisoryId: "", offset: 0 })}>{packageName} {resolvedVersion} ×</button> : null}
        {versionSpecifier ? <button type="button" className="filter-chip filter-chip--active" onClick={() => onFilter({ versionSpecifier: "", advisoryId: "", offset: 0 })}>{advisoryId ? `${advisoryId} · ` : ""}{versionSpecifier} ×</button> : null}
        {evidence !== "all" ? <button type="button" className="filter-chip filter-chip--active" onClick={() => onFilter({ evidence: "all", offset: 0 })}>{t(`dependencies.evidenceStates.${evidence}`)} ×</button> : null}
      </div> : null}
    </section>

    <section className="panel dependency-population-panel">
      <div className="section-heading"><div><span className="eyebrow">{t("dependencies.populationEyebrow")}</span><h2>{matching ? t("dependencies.matchingRepositories") : t(`dependencies.coveragePopulationTitles.${coverage}`)}</h2><p>{matching ? t("dependencies.populationDescription") : t("dependencies.coveragePopulationDescription")}</p></div>
        {(matchPage || coveragePage) ? <div className="dependency-selection-action"><strong>{t("dependencies.repositoryCount", { count: pageTotal })}</strong><button className="button button--primary" type="button" disabled={selecting || pageTotal === 0} onClick={onMaterialize}>{matching ? t("dependencies.selectAllMatching", { count: pageTotal }) : t("dependencies.selectAllPopulation", { count: pageTotal })}</button></div> : null}
      </div>
      <p className="dependency-selection-help">{t("dependencies.selectionHelp")}</p>
      {(matching ? repositories : coverageRepositories).status === "read-error" ? <p className="notice notice--warning" role="alert">{(matching ? repositories : coverageRepositories).failure?.message}</p> : null}
      {(matchPage || coveragePage) && pageLength === 0 ? <p className="dependency-empty">{t("dependencies.noRepositoryMatches")}</p> : null}
      {matchPage ? <div className="dependency-repository-list">{matchItems.map((match) => <RepositoryMatchCard key={match.repository.repository_id} match={match} onOpenEvidence={onOpenEvidence} onOpenRepository={onOpenRepository} />)}</div> : null}
      {coveragePage ? <div className="dependency-repository-list">{coverageItems.map((item) => <CoverageRepositoryCard key={item.repository.repository_id} item={item} packageName={packageName} onOpenEvidence={onOpenEvidence} onOpenRepository={onOpenRepository} />)}</div> : null}
      {(matchPage || coveragePage) && pageTotal > pageLimit ? <nav className="dependency-pagination" aria-label={t("dependencies.paginationLabel")}><button className="button button--secondary" type="button" disabled={pageOffset === 0} onClick={() => onFilter({ offset: Math.max(0, offset - pageLimit) })}>{t("dependencies.previous")}</button><span>{t("dependencies.pageRange", { from: pageOffset + 1, to: Math.min(pageOffset + pageLength, pageTotal), total: pageTotal })}</span><button className="button button--secondary" type="button" disabled={pageOffset + pageLimit >= pageTotal} onClick={() => onFilter({ offset: offset + pageLimit })}>{t("dependencies.next")}</button></nav> : null}
    </section>
  </section>;
}

function CoverageFact({ value, label, active = false, onClick }: { value: number; label: string; active?: boolean; onClick?: () => void }) {
  if (!onClick) return <span className="dependency-coverage-fact"><strong>{value}</strong><small>{label}</small></span>;
  return <button type="button" className={active ? "dependency-coverage-fact dependency-coverage-fact--active" : "dependency-coverage-fact"} onClick={onClick}><strong>{value}</strong><small>{label}</small></button>;
}

function RepositoryMatchCard({ match, onOpenEvidence, onOpenRepository }: { match: DependencyRepositoryMatch; onOpenEvidence: (runId: string, stepId: string) => void; onOpenRepository: (repositoryId: string) => void }) {
  const { t } = useTranslation();
  const versions = match.resolved_versions ?? [];
  return <article className="dependency-repository-card">
    <header><div><strong>{match.repository.name}</strong><small>{match.repository.path}</small></div><StatusBadge label={t(`dependencies.evidenceStates.${match.evidence.freshness}`)} tone={evidenceTone(match.evidence.freshness)} /></header>
    <div className="dependency-match-summary"><span><b>{t("dependencies.resolved")}</b>{versions.length ? versions.join(", ") : "—"}</span><span><b>{t("dependencies.contexts")}</b>{(match.contexts ?? []).join(", ")}</span></div>
    <details className="dependency-proof"><summary>{t("dependencies.whyMatched")}</summary><dl><div><dt>{t("dependencies.completeness")}</dt><dd>{t(`dependencies.completenessStates.${match.evidence.completeness}`)}</dd></div><div><dt>{t("dependencies.sources")}</dt><dd>{match.sources.join(", ")}</dd></div><div><dt>{t("dependencies.observed")}</dt><dd>{new Date(match.observed_at).toLocaleString()}</dd></div><div><dt>{t("dependencies.run")}</dt><dd><code>{match.fleet_run_id}</code></dd></div><div><dt>{t("dependencies.producer")}</dt><dd>{match.producer_goon ?? "—"}</dd></div><div><dt>{t("dependencies.step")}</dt><dd><code>{match.step_id}</code></dd></div></dl></details>
    <div className="button-row dependency-repository-actions"><button className="button button--primary button--small" type="button" onClick={() => onOpenEvidence(match.fleet_run_id, match.step_id)}>{t("dependencies.openEvidence")}</button><button className="button button--secondary button--small" type="button" onClick={() => onOpenRepository(match.repository.repository_id)}>{t("dependencies.repositoryDependencies")}</button></div>
  </article>;
}

function CoverageRepositoryCard({ item, packageName, onOpenEvidence, onOpenRepository }: { item: DependencyCoverageRepository; packageName: string; onOpenEvidence: (runId: string, stepId: string) => void; onOpenRepository: (repositoryId: string) => void }) {
  const { t } = useTranslation();
  const sources = item.sources ?? [];
  return <article className="dependency-repository-card">
    <header><div><strong>{item.repository.name}</strong><small>{item.repository.path}</small></div><StatusBadge label={t(`dependencies.snapshotStates.${item.evidence.freshness}`)} tone={evidenceTone(item.evidence.freshness)} />{item.evidence.availability !== "available" ? <StatusBadge label={t(`dependencies.availabilityStates.${item.evidence.availability}`)} tone="warning" /> : null}</header>
    <p className="dependency-coverage-help">{item.coverage === "absent_from_lock" ? t("dependencies.provenNoMatchReason", { package: packageName }) : t("dependencies.refreshEvidenceReason")}</p>
    {item.evidence.reason ? <p className="muted">{item.evidence.reason}</p> : null}
    {item.observed_at ? <details className="dependency-proof"><summary>{t("dependencies.observationDetails")}</summary><dl><div><dt>{t("dependencies.completeness")}</dt><dd>{t(`dependencies.completenessStates.${item.evidence.completeness}`)}</dd></div><div><dt>{t("dependencies.sources")}</dt><dd>{sources.length ? sources.join(", ") : "—"}</dd></div><div><dt>{t("dependencies.observed")}</dt><dd>{new Date(item.observed_at).toLocaleString()}</dd></div><div><dt>{t("dependencies.run")}</dt><dd>{item.fleet_run_id ? <code>{item.fleet_run_id}</code> : "—"}</dd></div><div><dt>{t("dependencies.producer")}</dt><dd>{item.producer_goon ?? "—"}</dd></div><div><dt>{t("dependencies.step")}</dt><dd>{item.step_id ? <code>{item.step_id}</code> : "—"}</dd></div></dl></details> : null}
    <div className="button-row dependency-repository-actions">{item.fleet_run_id && item.step_id ? <button className="button button--primary button--small" type="button" onClick={() => onOpenEvidence(item.fleet_run_id!, item.step_id!)}>{t("dependencies.openEvidence")}</button> : null}<button className="button button--secondary button--small" type="button" onClick={() => onOpenRepository(item.repository.repository_id)}>{t("dependencies.repositoryDependencies")}</button></div>
  </article>;
}
