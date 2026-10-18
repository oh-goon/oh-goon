import { useTranslation } from "react-i18next";

import type { FleetPlanTrustReceipt, OperationReport } from "@generated/models";
import { Verdict as VerdictValue } from "@generated/enums";
import { StatusBadge } from "@/components/common/StatusBadge";
import { verdictKey, verdictTone } from "@/domain/presentation/operations";
import { explanationKeys } from "@/domain/presentation/repositoryExplanations";
import { PlanTrustReceipt } from "@/features/history/components/PlanTrustReceipt";

interface OperationResultCardProps {
  report: OperationReport;
  trustReceipt?: FleetPlanTrustReceipt | null;
}

function technical(value: string | null | undefined): string {
  return value?.replaceAll("_", " ").toLowerCase() ?? "—";
}

export function OperationResultCard({ report, trustReceipt = null }: OperationResultCardProps) {
  const { t } = useTranslation();
  const explanation = explanationKeys(report.outcome.reason_code);
  const evidence = report.evidence;
  const dependencies = evidence.kind === "PLAN" || evidence.kind === "UPDATE" ? evidence.dependencies : null;
  const validation = evidence.kind === "PLAN" || evidence.kind === "UPDATE" || evidence.kind === "VERIFY" ? evidence.validation : null;
  const readiness = evidence.kind === "READINESS" ? evidence.readiness : null;
  const git = evidence.kind === "UPDATE" || evidence.kind === "REVIEW" ? evidence.git : null;
  const recipe = evidence.kind === "RECIPE" ? evidence.recipe : null;
  const migration = evidence.kind === "MIGRATION" ? evidence.migration : null;
  const unexpectedFiles = git?.worktree.unexpected_files ?? [];
  const policy = "policy" in evidence ? evidence.policy : null;
  const verdict = report.outcome.verdict ?? VerdictValue.UNKNOWN;

  return (
    <article className="operation-result-card">
      <div className="operation-result-heading">
        <div>
          <strong>{report.repository.name}</strong>
          <small>{report.repository.path}</small>
        </div>
        <StatusBadge
          label={t(verdictKey(verdict))}
          tone={verdictTone(verdict)}
        />
      </div>

      <div className="operation-result-explanation issue-guidance">
        <div><span>{t("attention.whatHappened")}</span><p>{t(explanation.what)}</p></div>
        <div><span>{t("attention.whatItMeans")}</span><p>{t(explanation.impact)}</p></div>
        <div><span>{t("attention.whatToDo")}</span><p>{t(explanation.nextAction)}</p></div>
      </div>

      {recipe ? (
        <details className="history-details">
          <summary>{t("history.commandOutput")}: {recipe.recipe}</summary>
          <dl className="technical-grid">
            <div><dt>{t("history.command")}</dt><dd>{recipe.argv.join(" ")}</dd></div>
            <div><dt>{t("history.exitCode")}</dt><dd>{recipe.exit_code}</dd></div>
            {recipe.sbom_component_count !== null && recipe.sbom_component_count !== undefined ? <div><dt>{t("history.sbomComponents")}</dt><dd>{recipe.sbom_component_count}</dd></div> : null}
          </dl>
          {recipe.output ? <pre className="command-output">{recipe.output}</pre> : null}
        </details>
      ) : null}

      {evidence.kind === "FLEET_PLAN" && trustReceipt ? <PlanTrustReceipt receipt={trustReceipt} /> : null}

      {migration ? (
        <details className="history-details" open>
          <summary>{t("history.migrationResult")}</summary>
          <dl className="technical-grid">
            <div><dt>{t("history.migrationSource")}</dt><dd>{migration.source}</dd></div>
            <div><dt>{t("history.branch")}</dt><dd>{migration.branch ?? "—"}</dd></div>
            <div><dt>{t("history.commit")}</dt><dd>{migration.commit ?? "—"}</dd></div>
          </dl>
          {(migration.changed_files ?? []).length ? <div className="history-file-list"><strong>{t("history.changedFiles")}</strong><ul>{(migration.changed_files ?? []).map((file) => <li key={file}>{file}</li>)}</ul></div> : null}
        </details>
      ) : null}

      {dependencies ? (
        <div className="operation-metrics">
          <span>{t("history.dependencyResult")}: {technical(dependencies.state)}</span>
        </div>
      ) : null}

      <details className="history-details">
        <summary>{t("history.technicalDetails")}</summary>
        <dl className="technical-grid">
          <div><dt>{t("history.operationId")}</dt><dd>{report.provenance.operation_id}</dd></div>
          <div><dt>{t("history.reasonCode")}</dt><dd>{report.outcome.reason_code}</dd></div>
          <div><dt>{t("history.execution")}</dt><dd>{technical(report.outcome.state)}</dd></div>
          <div><dt>{t("history.policy")}</dt><dd>{policy?.policy_id ?? "—"}</dd></div>
          {readiness ?
            <div><dt>{t("history.branch")}</dt><dd>{readiness.position.branch ?? technical(readiness.position.state)}</dd></div>
            : null}
          {git ? <>
            <div><dt>{t("history.branch")}</dt><dd>{git.position.branch ?? technical(git.position.state)}</dd></div>
            <div><dt>{t("history.gitFinal")}</dt><dd>{technical(git.final_state)}</dd></div>
          </> : null}
          {validation ? <div><dt>{t("history.validationResult")}</dt><dd>{technical(validation.state)}</dd></div> : null}
          {validation?.state === "repository_completed" ? <>
            <div><dt>{t("history.tests")}</dt><dd>{technical(validation.tests)}</dd></div>
            <div><dt>{t("history.audit")}</dt><dd>{technical(validation.security_audit)}</dd></div>
            <div><dt>{t("history.environment")}</dt><dd>{technical(validation.environment)}</dd></div>
          </> : null}
        </dl>
        {unexpectedFiles.length ? (
          <div className="history-file-list">
            <strong>{t("history.unexpectedFiles")}</strong>
            <ul>{unexpectedFiles.map((file) => <li key={file}>{file}</li>)}</ul>
          </div>
        ) : null}
      </details>
    </article>
  );
}
