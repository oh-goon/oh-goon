import { useTranslation } from "react-i18next";

import type { GameTape as GameTapeModel } from "@generated/models";
import { GameTapeStepArtifacts } from "@/features/game-tape/components/GameTapeStepArtifacts";

export function GameTapeOutputs({ tape }: { tape: GameTapeModel }) {
  const { t } = useTranslation();
  const entries = tape.repositories.flatMap((repository) =>
    (repository.steps ?? [])
      .filter((step) => (step.artifacts?.length ?? 0) > 0 || (step.outputs?.length ?? 0) > 0)
      .map((step) => ({ repository, step })),
  );

  return <section className="game-tape-outputs" aria-label={t("gameTape.outputs.title")}>
    <header><div><span className="eyebrow">{t("gameTape.outputs.eyebrow")}</span><h3>{t("gameTape.outputs.title")}</h3></div><p>{t("gameTape.outputs.contract")}</p></header>
    {entries.length === 0 ? <p className="muted">{t("gameTape.outputs.empty")}</p> : <div className="game-tape-outputs__entries">
      {entries.map(({ repository, step }) => <article key={step.step_execution_id} className="game-tape-outputs__entry">
        <header><div><strong>{repository.repository.name}</strong><code>{step.step_id}</code></div></header>
        {(step.outputs?.length ?? 0) > 0 ? <section aria-label={t("gameTape.outputs.published")}>
          <strong>{t("gameTape.outputs.published")}</strong>
          <ul>{(step.outputs ?? []).map((output) => <li key={output.name}><code>{output.name}</code><span>{output.byte_count} B</span><code title={output.sha256}>{output.sha256.slice(0, 12)}</code></li>)}</ul>
        </section> : null}
        <GameTapeStepArtifacts artifacts={step.artifacts ?? []} ownerIdentity={step.step_execution_id} />
      </article>)}
    </div>}
  </section>;
}
