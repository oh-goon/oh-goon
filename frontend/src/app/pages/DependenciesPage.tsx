import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient, useMutationBusy, useReportMutationFeedback } from "@/app/ApiClientProvider";
import { appDiagnosticStore } from "@/domain/diagnostics/appDiagnosticStore";
import { readLocationHash, type AppPage } from "@/app/navigation";
import { dependencyQuery, dependencyReviewStateFromHash, type DependencyReviewState } from "@/app/pages/dependencyReviewState";
import { PageHeading } from "@/components/common/PageHeading";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import { advanceIdentityIncarnation, sameIdentityIncarnation, type IdentityIncarnation } from "@/domain/concurrency/identityIncarnation";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";
import type { DependencyTargetFilter, DependencyTargetMaterialization } from "@generated/models";
import {
  DependencyPackagePanel,
  DependencySearchPanel,
  RepositoryDependenciesPanel,
  useDependencyOverview,
  useDependencyAdvisory,
  useDependencyCoverageRepositories,
  useDependencyRepositories,
  useDependencySearch,
  useRepositoryDependencies,
} from "@/features/dependencies/DependenciesFeature";

interface Props {
  navigate: (page: AppPage, query?: Readonly<Record<string, string>>) => void;
}

export function DependenciesPage({ navigate }: Props) {
  const { t } = useTranslation();
  const client = useApiClient();
  const reportSelection = useReportMutationFeedback("dependency-selections");
  const mutationBusy = useMutationBusy();
  const state = dependencyReviewStateFromHash(readLocationHash());
  const search = useDependencySearch(state.q, state.offset);
  const overview = useDependencyOverview(state.packageName);
  const advisory = useDependencyAdvisory({ packageName: state.packageName, advisoryId: state.advisoryId, affectedSpecifier: state.versionSpecifier });
  const repositories = useDependencyRepositories({
    packageName: state.coverage === "matching" ? state.packageName : "",
    resolvedVersion: state.resolvedVersion,
    versionSpecifier: state.versionSpecifier,
    advisoryId: state.advisoryId,
    relationship: state.relationship,
    evidence: state.evidence,
    offset: state.offset,
  });
  const coverageRepositories = useDependencyCoverageRepositories({
    packageName: state.packageName,
    coverage: state.coverage,
    offset: state.offset,
  });
  const repositoryDependencies = useRepositoryDependencies(state.repositoryId, state.offset);
  const mountedRef = useRef(true);
  const materializeOwnerRef = useRef<IdentityIncarnation>({ identity: "", generation: 0 });
  const populationId = state.coverage === "matching"
    ? repositories.data?.population_id ?? null
    : coverageRepositories.data?.population_id ?? null;
  const materializeIdentity = canonicalIdentity({
    populationId,
    packageName: state.packageName,
    coverage: state.coverage,
    relationship: state.coverage === "matching" ? state.relationship : "all",
    evidence: state.coverage === "matching" ? state.evidence : "all",
    resolvedVersion: state.coverage === "matching" ? state.resolvedVersion : "",
    versionSpecifier: state.coverage === "matching" ? state.versionSpecifier : "",
    advisoryId: state.coverage === "matching" ? state.advisoryId : "",
  });
  materializeOwnerRef.current = advanceIdentityIncarnation(materializeOwnerRef.current, materializeIdentity);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const go = (next: Partial<DependencyReviewState>) => navigate("dependencies", dependencyQuery(next));
  const openEvidence = (runId: string, stepId: string) => navigate("runs", { run: runId, step: stepId });
  const materialize = async () => {
    if (!state.packageName || populationId === null) return;
    const commandOwner = materializeOwnerRef.current;
    const matching = state.coverage === "matching";
    const filter: DependencyTargetFilter = {
      package_name: state.packageName,
      coverage: state.coverage,
      relationship: matching ? state.relationship : "all",
      evidence: matching ? state.evidence : "all",
      ...(matching && state.resolvedVersion ? { resolved_version: state.resolvedVersion } : {}),
      ...(matching && state.versionSpecifier ? { version_specifier: state.versionSpecifier } : {}),
      ...(matching && state.advisoryId ? { advisory_id: state.advisoryId } : {}),
    };
    const result = validateMaterializedDependencyIdentity(
      await client.materializeDependencyTargets({ expected_population_id: populationId, filter }),
      populationId,
      filter,
    );
    reportSelection(result);
    if (result.outcome === "saved" && mountedRef.current && sameIdentityIncarnation(materializeOwnerRef.current, commandOwner)) {
      navigate("shared-runs", { selection: result.value.query_id });
    }
  };

  return <main className="page">
    <PageHeading eyebrow={t("dependencies.eyebrow")} title={t("dependencies.title")} description={t("dependencies.description")} />
    <div className="notice notice--scope dependency-truth-notice"><strong>{t("dependencies.truthTitle")}</strong><p>{t("dependencies.truthMessage")}</p></div>
    {state.repositoryId ? <RepositoryDependenciesPanel
      repositoryId={state.repositoryId}
      resource={repositoryDependencies}
      onBack={() => go({ q: state.q, packageName: state.packageName, offset: 0 })}
      onPage={(offset) => go({ ...state, offset })}
      onOpenPackage={(packageName) => go({ packageName, q: packageName, offset: 0 })}
      onOpenEvidence={openEvidence}
    /> : state.packageName ? <DependencyPackagePanel
      packageName={state.packageName}
      resolvedVersion={state.resolvedVersion}
      versionSpecifier={state.versionSpecifier}
      advisoryId={state.advisoryId}
      relationship={state.relationship}
      evidence={state.evidence}
      coverage={state.coverage}
      offset={state.offset}
      overview={overview}
      advisory={advisory}
      repositories={repositories}
      coverageRepositories={coverageRepositories}
      selecting={mutationBusy}
      onBack={() => go({ q: state.q || state.packageName, offset: 0 })}
      onFilter={(next) => go({ ...state, repositoryId: "", ...next })}
      onMaterialize={() => void materialize()}
      onOpenEvidence={openEvidence}
      onOpenRepository={(repositoryId) => go({ ...state, repositoryId, offset: 0 })}
    /> : <DependencySearchPanel
      query={state.q}
      resource={search}
      onSearch={(q) => go({ q, offset: 0 })}
      onPage={(offset) => go({ q: state.q, offset })}
      onOpenPackage={(packageName) => go({ q: state.q || packageName, packageName, offset: 0 })}
    />}
  </main>;
}

function validateMaterializedDependencyIdentity(
  result: WriteCommandResult<DependencyTargetMaterialization>,
  expectedPopulationId: string,
  expectedFilter: DependencyTargetFilter,
): WriteCommandResult<DependencyTargetMaterialization> {
  if (result.outcome !== "saved") return result;
  if (result.value.query_id === expectedPopulationId
    && canonicalIdentity(result.value.filter) === canonicalIdentity(expectedFilter)) return result;
  const diagnostic = appDiagnosticStore.record({
    source: "dependency-target-materialization-identity",
    error: new Error("Dependency target materialization response does not match the requested population/filter identity"),
    scope: "dependency-selections",
    causalityRevision: result.causalityRevision,
  });
  return { outcome: "write-error", diagnosticId: diagnostic.id, recovery: "refresh" };
}
