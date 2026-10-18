import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useMutationBusy } from "@/app/ApiClientProvider";
import { AppDiagnosticsPanel } from "@/app/diagnostics/AppDiagnosticsPanel";
import { useFleetRunStatus, useFleetSnapshot } from "@/app/FleetRunProvider";
import { LanguageSwitcher } from "@/app/LanguageSwitcher";
import type { ExperienceMode } from "@/app/preferences";
import { ReadStateNotice } from "@/app/reads/ReadStateNotice";
import { ErrorBanner } from "@/components/common/ErrorBanner";
import { PageHeading } from "@/components/common/PageHeading";
import { HockeyPersonaSpotlight } from "@/features/hockey/HockeyTeamFeature";
import { LocationManager, useLocations } from "@/features/locations/LocationsFeature";
import { SnapshotProvenance } from "@/features/overview/OverviewFeature";
import { PrivacyNotice, usePrivacyResponse } from "@/features/privacy/PrivacyFeature";
import { SystemHealthPanel, useSystemHealth } from "@/features/system-health/SystemHealthFeature";

export function SettingsPage({ experience, onExperienceChange, onOpenRepositories }: { experience: ExperienceMode; onExperienceChange: (mode: ExperienceMode) => void; onOpenRepositories: () => void }) {
  const { t } = useTranslation();
  const runStatus = useFleetRunStatus();
  const mutationBusy = useMutationBusy();
  const health = useSystemHealth();
  const privacy = usePrivacyResponse();
  const locations = useLocations();
  const fleet = useFleetSnapshot();
  const [advancedOpen, setAdvancedOpen] = useState(experience === "advanced");
  useEffect(() => { setAdvancedOpen(experience === "advanced"); }, [experience]);
  const blockingError = [health.resource, privacy.resource, locations.resource, fleet.resource]
    .find((resource) => resource.status === "read-error")?.failure?.message ?? runStatus.activeRunError;
  const locationValues = locations.resource.data?.locations ?? [];
  const unavailableLocations = locations.resource.data?.unavailable ?? [];

  return <main className="page page--settings"><PageHeading eyebrow={t("settings.eyebrow")} title={t("settings.title")} description={t("settings.description")} action={<button className="button button--secondary" type="button" onClick={onOpenRepositories}>{t("settings.openRepositories")}</button>} />
    {blockingError ? <ErrorBanner message={blockingError} /> : null}
    <ReadStateNotice resource={locations.resource} onRefresh={() => void locations.refresh()} />
    <section className="panel settings-section"><div className="section-heading"><div><h2>{t("settings.general.title")}</h2><p>{t("settings.general.description")}</p></div></div><div className="setting-row"><div><strong>{t("settings.language.title")}</strong><p>{t("settings.language.description")}</p></div><LanguageSwitcher /></div><div className="setting-row"><div><strong>{t("settings.experience.title")}</strong><p>{t("settings.experience.description")}</p></div><div className="segmented-control"><button className={experience === "standard" ? "segmented-control__item segmented-control__item--active" : "segmented-control__item"} type="button" onClick={() => onExperienceChange("standard")}>{t("settings.experience.standard")}</button><button className={experience === "advanced" ? "segmented-control__item segmented-control__item--active" : "segmented-control__item"} type="button" onClick={() => onExperienceChange("advanced")}>{t("settings.experience.advanced")}</button></div></div></section>
    <LocationManager locations={locationValues} revision={locations.resource.data?.revision ?? ""} unavailableLocations={unavailableLocations} actionsDisabled={runStatus.activeRun !== null || mutationBusy || locations.resource.status !== "read-fresh"} />
    <section className="panel advanced-settings"><button className="advanced-toggle" type="button" aria-expanded={advancedOpen} aria-controls="advanced-settings-content" onClick={() => setAdvancedOpen((value) => !value)}><div><span className="eyebrow">{t("settings.advanced.eyebrow")}</span><strong>{t("settings.advanced.title")}</strong><small>{t("settings.advanced.description")}</small></div><span>{advancedOpen ? "−" : "+"}</span></button>{advancedOpen ? <div id="advanced-settings-content" className="advanced-settings__content"><HockeyPersonaSpotlight persona="equipmentManager" /><SystemHealthPanel health={health.resource.data} /><PrivacyNotice privacy={privacy.resource.data} /><SnapshotProvenance fleet={fleet.resource.data} /><AppDiagnosticsPanel /><div className="notice notice--scope"><strong>{t("settings.advanced.databaseTitle")}</strong><p>{t("settings.advanced.databaseMessage")}</p></div></div> : null}</section>
  </main>;
}
