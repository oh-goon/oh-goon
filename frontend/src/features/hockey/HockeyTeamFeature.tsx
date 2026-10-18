import { useTranslation } from "react-i18next";

import type { RecoveryStatus } from "@generated/models";
import { HOCKEY_TEAM, type HockeyPersonaId } from "@/domain/presentation/hockeyTeam";

type PersonaField = "name" | "tagline" | "owner" | "boundary";
type PersonaKey = `hockey.personas.${HockeyPersonaId}.${PersonaField}`;

function personaKey(id: HockeyPersonaId, field: PersonaField): PersonaKey {
  return `hockey.personas.${id}.${field}`;
}

export function HockeyPersonaSpotlight({ persona }: { persona: HockeyPersonaId }) {
  const { t } = useTranslation();
  return <aside className="hockey-persona-spotlight">
    <span className="eyebrow">{t("hockey.eyebrow")}</span>
    <div><strong>{t(personaKey(persona, "name"))}</strong><span>{t(personaKey(persona, "tagline"))}</span></div>
    <small>{t(personaKey(persona, "boundary"))}</small>
  </aside>;
}

export function HockeyPenaltyBoxNotice({ status }: { status: RecoveryStatus | null }) {
  const { t } = useTranslation();
  const count = status?.effect_quarantines?.length ?? 0;
  if (count === 0) return null;
  return <aside className="hockey-penalty-box" role="status">
    <div><span className="eyebrow">{t("hockey.personas.penaltyBox.name")}</span><strong>{t("hockey.penaltyBox.title", { count })}</strong></div>
    <p>{t("hockey.penaltyBox.description")}</p>
  </aside>;
}

export function HockeyRoster() {
  const { t } = useTranslation();
  return <div className="hockey-roster">
    <div className="hockey-roster__header">
      <div>
        <span className="eyebrow">{t("hockey.eyebrow")}</span>
        <h3>{t("hockey.roster.title")}</h3>
      </div>
      <span className="hockey-roster__rule">{t("hockey.roster.rule")}</span>
    </div>
    <div className="hockey-roster__grid">
      {HOCKEY_TEAM.map((persona) => <article className="hockey-roster-card" key={persona.id}>
        <div className="hockey-roster-card__title">
          <strong>{t(personaKey(persona.id, "name"))}</strong>
          {persona.optional ? <span>{t("hockey.optional")}</span> : null}
        </div>
        <p>{t(personaKey(persona.id, "tagline"))}</p>
        <dl>
          <div><dt>{t("hockey.roster.owner")}</dt><dd>{t(personaKey(persona.id, "owner"))}</dd></div>
          <div><dt>{t("hockey.roster.boundary")}</dt><dd>{t(personaKey(persona.id, "boundary"))}</dd></div>
        </dl>
      </article>)}
    </div>
  </div>;
}
