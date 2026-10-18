import { useTranslation } from "react-i18next";

import type { FleetSnapshot, OperationReport } from "@generated/models";

interface SnapshotProvenanceProps {
  fleet: FleetSnapshot | null;
}

function shortId(value: string | null | undefined): string {
  if (!value) return "—";
  return value.length > 24 ? `${value.slice(0, 20)}…` : value;
}

export function SnapshotProvenance({ fleet }: SnapshotProvenanceProps) {
  const { i18n, t } = useTranslation();
  if (!fleet) return null;

  const provenance = fleet.provenance;
  const generatedAt = new Intl.DateTimeFormat(i18n.language, {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(new Date(provenance.generated_at));
  const latest = fleet.latest_fleet_runs ?? [];
  const latestTitle = latest
    .map((detail) => `${detail.run.capability.capability_id}: ${detail.run.fleet_run_id}`)
    .join("\n");
  const reports = [
    ...(fleet.repositories ?? []).flatMap((repository) =>
      [repository.init, repository.plan, repository.update, repository.verify]
        .filter((state) => state != null)
        .map((state) => state.report),
    ),
    ...latest.flatMap((detail) => detail.operations ?? []),
    ...(fleet.active_fleet_run?.operations ?? []),
  ];
  const policyIds = [...new Set(reports.flatMap((report) => {
    const policyId = operationPolicyId(report);
    return policyId === null ? [] : [policyId];
  }))].sort();

  return (
    <section className="panel provenance-strip" aria-label={t("provenance.ariaLabel")}>
      <div>
        <span className="eyebrow">{t("provenance.title")}</span>
        <p>{t("provenance.description")}</p>
      </div>
      <dl className="provenance-grid">
        <div><dt>{t("provenance.snapshot")}</dt><dd><code title={provenance.snapshot_id}>{shortId(provenance.snapshot_id)}</code></dd></div>
        <div><dt>{t("provenance.durableRevision")}</dt><dd><code>{provenance.durable_revision}</code></dd></div>
        <div><dt>{t("provenance.journalRevision")}</dt><dd><code>{provenance.journal_revision}</code></dd></div>
        <div><dt>{t("provenance.generated")}</dt><dd>{generatedAt}</dd></div>
        <div><dt>{t("provenance.latestRuns")}</dt><dd title={latestTitle}>{latest.length}</dd></div>
        <div>
          <dt>{t("provenance.policies")}</dt>
          <dd title={policyIds.join("\n")}>{policyIds.length}</dd>
        </div>
      </dl>
    </section>
  );
}

function operationPolicyId(report: OperationReport): string | null {
  return "policy" in report.evidence ? report.evidence.policy?.policy_id ?? null : null;
}
