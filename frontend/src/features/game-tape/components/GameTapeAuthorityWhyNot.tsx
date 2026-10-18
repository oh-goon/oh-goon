import { useTranslation } from "react-i18next";

import type { GameTape } from "@generated/models";

export function GameTapeAuthorityWhyNot({ tape }: { tape: GameTape }) {
  const { t } = useTranslation();
  const diagnostic = tape.why_not;
  if (!diagnostic) return null;
  return <section className="compact-card game-tape-why-not" aria-label={t("gameTape.whyNot.title")}>
    <div>
      <span className="eyebrow">{t("gameTape.whyNot.eyebrow")}</span>
      <h3>{t("gameTape.whyNot.title")}</h3>
      <p>{diagnostic.message}</p>
    </div>
    <dl className="game-tape-inspector__facts">
      <div><dt>{t("gameTape.whyNot.code")}</dt><dd><code>{diagnostic.code}</code></dd></div>
      <div><dt>{t("gameTape.whyNot.stage")}</dt><dd>{diagnostic.stage}</dd></div>
      <div><dt>{t("gameTape.whyNot.owner")}</dt><dd>{diagnostic.authority_owner}</dd></div>
      <div><dt>{t("gameTape.whyNot.retryable")}</dt><dd>{diagnostic.retryable ? t("common.yes") : t("common.no")}</dd></div>
    </dl>
    <p><strong>{t("gameTape.whyNot.nextAction")}</strong> {diagnostic.safe_next_action}</p>
    <small>{t("gameTape.whyNot.rule")}</small>
  </section>;
}
