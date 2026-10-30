import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { GameTapeArtifactObservation } from "@generated/models";
import { matchingArtifactObservation, type GameTapeArtifact, type GameTapeArtifactOwner } from "@/features/game-tape/gameTapeArtifacts";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";

export function GameTapeStepArtifacts({ artifacts, ownerIdentity, artifactOwner, observation }: { artifacts: readonly GameTapeArtifact[]; ownerIdentity?: string; artifactOwner?: GameTapeArtifactOwner; observation?: GameTapeArtifactObservation | null }) {
  const { t } = useTranslation();
  const renderOwner = ownerIdentity ?? canonicalIdentity({ artifacts });
  const currentOwner = useRef(renderOwner);
  currentOwner.current = renderOwner;
  const [copied, setCopied] = useState<{ owner: string; key: string } | null>(null);
  if (artifacts.length === 0) return null;

  const copyFact = (key: string, value: string) => {
    const owner = renderOwner;
    void navigator.clipboard.writeText(value).then(
      () => { if (currentOwner.current === owner) setCopied({ owner, key }); },
      () => { if (currentOwner.current === owner) setCopied(null); },
    );
  };

  return <section className="game-tape-artifacts" aria-label={t("gameTape.evidence.artifacts")}>
    <header><div><span className="eyebrow">{t("gameTape.evidence.artifacts")}</span><strong>{t("gameTape.evidence.artifactCount", { count: artifacts.length })}</strong></div><small>{t("gameTape.evidence.artifactRule")}</small></header>
    <div>{artifacts.map((artifact) => {
      const present = artifact.status === "PRESENT" && "sha256" in artifact;
      const availability = matchingArtifactObservation(observation, artifactOwner, artifact);
      const digestCopyKey = `${artifact.path}:sha256`;
      const referenceCopyKey = `${artifact.path}:reference`;
      return <article key={artifact.path}>
        <span aria-hidden="true">{present ? "✓" : "!"}</span>
        <div>
          <b>{artifact.path}</b>
          {availability ? <div role="status">
            <strong>{t(`gameTape.evidence.availability.${availability.status}`)}</strong>
            <small>{t(`gameTape.evidence.availabilityReason.${availability.reason}`)}</small>
            <small>{t("gameTape.evidence.checkedAt", { time: availability.checked_at })}</small>
            {availability.staging_cleanup === "FAILED" ? <small>{t("gameTape.evidence.stagingCleanupFailed")}</small> : null}
          </div> : <small>{t("gameTape.evidence.availabilityNotChecked")}</small>}
          {present ? <>
            <small>{t("gameTape.evidence.retainedArtifact", { bytes: artifact.byte_count, digest: artifact.sha256.slice(0, 12) })}</small>
            <div className="button-row">
              <button type="button" className="button button--secondary button--compact" onClick={() => copyFact(digestCopyKey, artifact.sha256)}>{copied?.owner === renderOwner && copied.key === digestCopyKey ? t("gameTape.evidence.digestCopied") : t("gameTape.evidence.copyDigest")}</button>
              {artifact.retained_object ? <button type="button" className="button button--secondary button--compact" onClick={() => copyFact(referenceCopyKey, artifact.retained_object!.reference_id)}>{copied?.owner === renderOwner && copied.key === referenceCopyKey ? t("gameTape.evidence.referenceCopied") : t("gameTape.evidence.copyReference")}</button> : null}
            </div>
            <details className="advanced-disclosure">
              <summary>{t("gameTape.evidence.artifactDetails")}</summary>
              <dl>
                <div><dt>{t("gameTape.evidence.path")}</dt><dd><code>{artifact.path}</code></dd></div>
                <div><dt>{t("gameTape.logs.bytes")}</dt><dd>{artifact.byte_count}</dd></div>
                <div><dt>SHA-256</dt><dd><code>{artifact.sha256}</code></dd></div>
                <div><dt>{t("gameTape.evidence.retention")}</dt><dd>{artifact.retained_object ? t("gameTape.evidence.retainedDurably") : t("gameTape.evidence.noRetainedReference")}</dd></div>
                {artifact.retained_object ? <>
                  <div><dt>{t("gameTape.evidence.referenceId")}</dt><dd><code>{artifact.retained_object.reference_id}</code></dd></div>
                  <div><dt>{t("gameTape.evidence.ownerRepository")}</dt><dd><code>{artifact.retained_object.owner_repository_id}</code></dd></div>
                  <div><dt>{t("gameTape.evidence.object")}</dt><dd><code>{artifact.retained_object.kind} · {artifact.retained_object.object_id}</code></dd></div>
                  <div><dt>{t("gameTape.evidence.entries")}</dt><dd>{artifact.retained_object.entry_count}</dd></div>
                </> : null}
              </dl>
              <small>{t("gameTape.evidence.previewUnavailable")}</small>
            </details>
          </> : <small role="alert">{t("gameTape.evidence.missingArtifact")}</small>}
        </div>
      </article>;
    })}</div>
  </section>;
}
