import { useTranslation } from "react-i18next";

import { PageHeading } from "@/components/common/PageHeading";
import { HockeyRoster } from "@/features/hockey/HockeyTeamFeature";

export function HelpPage({ onRepositories }: { onRepositories: () => void }) {
  const { t } = useTranslation();
  const sections = ["gettingStarted", "firstUpdate", "issues", "results", "review", "hockeyTeam", "advanced", "troubleshooting"] as const;
  return <main className="page page--help"><PageHeading eyebrow={t("help.eyebrow")} title={t("help.title")} description={t("help.description")} action={<button className="button button--primary" type="button" onClick={onRepositories}>{t("help.backToRepositories")}</button>} /><section className="help-layout"><nav className="help-nav">{sections.map((section) => <button type="button" key={section} onClick={() => document.getElementById(`help-${section}`)?.scrollIntoView({ behavior: "smooth", block: "start" })}>{t(`help.sections.${section}.title`)}</button>)}</nav><div className="help-content">{sections.map((section) => <article id={`help-${section}`} className="help-article" key={section}><h2>{t(`help.sections.${section}.title`)}</h2><p>{t(`help.sections.${section}.body`)}</p>{section === "gettingStarted" ? <ol><li>{t("help.steps.repositories")}</li><li>{t("help.steps.sharedRuns")}</li><li>{t("help.steps.schedules")}</li><li>{t("help.steps.runs")}</li></ol> : null}{section === "hockeyTeam" ? <HockeyRoster /> : null}{section === "issues" ? <div className="help-callout"><strong>{t("help.issueRule")}</strong><span>{t("help.issueRuleMessage")}</span></div> : null}</article>)}</div></section></main>;
}
