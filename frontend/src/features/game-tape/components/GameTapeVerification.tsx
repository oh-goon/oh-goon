import { useTranslation } from "react-i18next";

import type { GameTape, GameTapeRepositoryExecution } from "@generated/models";

export function GameTapeVerification({ tape }: { tape: GameTape }) {
  const { t } = useTranslation();
  const repositories = tape.repositories.filter((repository) => repository.verification_execution != null);

  if (repositories.length === 0) {
    return <section className="notice notice--scope" aria-label={t("gameTape.verification.title")}>
      <strong>{t("gameTape.verification.title")}</strong>
      <p>{t("gameTape.verification.unavailable")}</p>
    </section>;
  }

  return <div className="game-tape-verification">
    <section className="notice notice--scope">
      <span className="eyebrow">{t("gameTape.verification.eyebrow")}</span>
      <h3>{t("gameTape.verification.heading")}</h3>
      <p>{t("gameTape.verification.contract")}</p>
    </section>
    {repositories.map((repository) => <RepositoryVerification key={repository.repository_execution_id} repository={repository} />)}
  </div>;
}

function RepositoryVerification({ repository }: { repository: GameTapeRepositoryExecution }) {
  const { t } = useTranslation();
  const boundary = repository.verification_execution;
  if (boundary == null) return null;
  const selectedMembers = boundary.selected_members ?? [];
  const actualExecutedMembers = boundary.actual_executed_members ?? [];
  const unprovenOwnerMembers = boundary.unproven_owner_members ?? [];
  const executed = new Set(actualExecutedMembers);
  const memberStatus = verificationMemberStatuses(repository);

  return <section className="compact-card game-tape-verification__repository">
    <header>
      <div><span className="eyebrow">{t("gameTape.verification.repository")}</span><h4>{repository.repository.name}</h4></div>
      <strong>{boundary.selection_state ?? "ADMITTED_FRESH"}</strong>
    </header>
    <dl className="game-tape-summary__facts">
      <div><dt>{t("gameTape.verification.producer")}</dt><dd>{boundary.producer_version}</dd></div>
      <div><dt>{t("gameTape.verification.artifactIdentity")}</dt><dd><code>{boundary.producer_artifact_identity}</code></dd></div>
      <div><dt>{t("gameTape.verification.implementationIdentity")}</dt><dd><code>{boundary.producer_implementation_identity ?? "—"}</code></dd></div>
      <div><dt>{t("gameTape.verification.contractIdentity")}</dt><dd><code>{boundary.producer_contract_identity ?? "—"}</code></dd></div>
      <div><dt>{t("gameTape.verification.repositoryIdentity")}</dt><dd><code>{boundary.repository_identity}</code></dd></div>
      <div><dt>{t("gameTape.verification.revisionIdentity")}</dt><dd><code>{boundary.revision_identity}</code></dd></div>
      <div><dt>{t("gameTape.verification.selectionIdentity")}</dt><dd><code>{boundary.selection_evidence_identity}</code></dd></div>
      <div><dt>{t("gameTape.verification.freshness")}</dt><dd><strong>{boundary.producer_freshness_state ?? "PROVEN_FRESH"}</strong></dd></div>
      <div><dt>{t("gameTape.verification.provenanceIdentity")}</dt><dd><code>{boundary.producer_provenance_identity}</code></dd></div>
      <div><dt>{t("gameTape.verification.repositoryGeneration")}</dt><dd>{boundary.producer_repository_generation ?? "—"}</dd></div>
      <div><dt>{t("gameTape.verification.identityGeneration")}</dt><dd>{boundary.producer_identity_generation ?? "—"}</dd></div>
      <div><dt>{t("gameTape.verification.membershipIdentity")}</dt><dd><code>{boundary.selected_membership_identity}</code></dd></div>
      <div><dt>{t("gameTape.verification.executionMembershipIdentity")}</dt><dd><code>{boundary.actual_executed_membership_identity}</code></dd></div>
    </dl>
    {boundary.producer_implementation_provenance ? <details><summary>{t("gameTape.verification.implementationProvenance")}</summary>
      <p className="muted">{t("gameTape.verification.implementationProvenanceContract")}</p>
      <dl className="game-tape-summary__facts">
        <div><dt>{t("gameTape.verification.provenanceAuthority")}</dt><dd><strong>{boundary.producer_implementation_provenance.authority}</strong></dd></div>
        <div><dt>{t("gameTape.verification.provenanceStorage")}</dt><dd>{boundary.producer_implementation_provenance.storage}</dd></div>
        <div><dt>{t("gameTape.verification.provenanceInputs")}</dt><dd>{boundary.producer_implementation_provenance.input_count}</dd></div>
      </dl>
      <ul className="compact-list">{(boundary.producer_implementation_provenance.inputs ?? []).map((input) => <li key={input.path}><code>{input.path}</code><small>{input.byte_count} B · {input.content_sha256}</small></li>)}</ul>
    </details> : null}
    {(boundary.producer_evidence_hashes ?? []).length > 0 ? <details><summary>{t("gameTape.verification.evidenceHashes")}</summary><ul className="compact-list">{(boundary.producer_evidence_hashes ?? []).map((row, index) => <li key={`${index}:${String(row[0])}`}><strong>{String(row[0])}</strong><code>{String(row[1])}</code></li>)}</ul></details> : null}
    <div className="compact-metrics">
      <span><strong>{selectedMembers.length}</strong>{t("gameTape.verification.selected")}</span>
      <span><strong>{actualExecutedMembers.length}</strong>{t("gameTape.verification.executed")}</span>
      <span><strong>{unprovenOwnerMembers.length}</strong>{t("gameTape.verification.unproven")}</span>
    </div>
    {selectedMembers.length === 0 ? <p className="muted">{t("gameTape.verification.membersUnavailable")}</p> : <ol className="compact-list">
      {selectedMembers.map((member) => <li key={member}>
        <code>{member}</code>
        <small>{t("gameTape.verification.memberResult", {
          status: memberStatus.get(member) ?? (executed.has(member) ? t("gameTape.verification.executedFallback") : t("gameTape.verification.notExecuted")),
        })}</small>
      </li>)}
    </ol>}
    <p className="muted">{boundary.membership_complete ? t("gameTape.verification.complete") : t("gameTape.verification.incomplete")}</p>
  </section>;
}

function verificationMemberStatuses(repository: GameTapeRepositoryExecution): Map<string, string> {
  const result = new Map<string, { depth: number; status: string }>();
  for (const shard of repository.shard_lineage ?? []) {
    for (const member of shard.members ?? []) {
      const current = result.get(member);
      const refinementDepth = shard.refinement_depth ?? 0;
      if (current === undefined || refinementDepth >= current.depth) {
        result.set(member, { depth: refinementDepth, status: shard.status });
      }
    }
  }
  return new Map([...result].map(([member, value]) => [member, value.status]));
}
