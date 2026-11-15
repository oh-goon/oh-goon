import { useTranslation } from "react-i18next";

import type { GameTape as GameTapeModel } from "@generated/models";
import { operations } from "@generated/operations";
import { operationHref } from "@/domain/api/operationHref";

export function GameTapeEnforcer({ tape }: { tape: GameTapeModel }) {
  const { t } = useTranslation();
  return <section className="game-tape-enforcer" aria-label={t("gameTape.enforcer.title")}>
    <header><div><span className="eyebrow">{t("gameTape.enforcer.eyebrow")}</span><h3>{t("gameTape.enforcer.title")}</h3></div></header>
    <p>{t("gameTape.enforcer.contract")}</p>
    {tape.enforcer_receipt_available ? <a
      className="button button--secondary"
      href={operationHref(operations.get_fleet_run_enforcer_receipt.operationId, { path: { fleet_run_id: tape.fleet_run_id } })}
      download={`${tape.fleet_run_id}-enforcer.yml`}
    >{t("gameTape.enforcer.download")}</a> : <p className="muted">{t("gameTape.enforcer.unavailable")}</p>}
  </section>;
}
