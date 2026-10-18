import { type ReactNode, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

import { useMutationFeedbackState } from "@/app/ApiClientProvider";
import { MutationFeedback } from "@/app/mutations/MutationFeedback";
import { readLocationHash, type AppPage } from "@/app/navigation";
import { LanguageSwitcher } from "@/app/LanguageSwitcher";
import type { ExperienceMode } from "@/app/preferences";

interface Props {
  page: AppPage;
  experience: ExperienceMode;
  onNavigate: (page: AppPage) => void;
  onRefresh: () => void;
  children: ReactNode;
}

const STANDARD_NAV: AppPage[] = ["overview", "repositories", "dependencies", "plans", "runs", "schedules", "scout"];
const ADVANCED_NAV: AppPage[] = [...STANDARD_NAV, "recovery", "tools"];
const UTILITY_NAV: AppPage[] = ["settings", "help"];

export function AppShell({ page, experience, onNavigate, onRefresh, children }: Props) {
  const { t } = useTranslation();
  const { result: mutationResult, clear: clearMutationFeedback } = useMutationFeedbackState();
  const primaryNav = experience === "advanced" ? ADVANCED_NAV : STANDARD_NAV;
  const locationIdentity = readLocationHash();
  const primaryNavPage: AppPage = page === "shared-runs" ? "runs" : page === "failures" ? "overview" : page;
  const previousRouteIdentity = useRef(`${page}:${locationIdentity}`);
  useEffect(() => {
    const routeIdentity = `${page}:${locationIdentity}`;
    const routeChanged = previousRouteIdentity.current !== routeIdentity;
    clearMutationFeedback();
    document.title = `${t(`shell.pages.${page}`)} · oh-goon`;
    window.scrollTo(0, 0);
    if (routeChanged) document.querySelector<HTMLElement>("main h1")?.focus({ preventScroll: true });
    previousRouteIdentity.current = routeIdentity;
  }, [clearMutationFeedback, locationIdentity, page, t]);
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">{t("shell.skipToContent")}</a>
      <header className="app-bar">
        <button className="brand-button" type="button" onClick={() => onNavigate("overview")}>
          <span className="brand-mark" aria-hidden="true">OG</span>
          <span><strong>oh-goon</strong><small>{t("shell.localApp")}</small></span>
        </button>
        <nav className="app-nav" aria-label={t("shell.navigation")}>
          {primaryNav.map((item) => (
            <button
              className={item === primaryNavPage ? "app-nav__item app-nav__item--active" : "app-nav__item"}
              key={item}
              type="button"
              aria-current={item === primaryNavPage ? "page" : undefined}
              onClick={() => onNavigate(item)}
            >
              {t(`shell.pages.${item}`)}
            </button>
          ))}
        </nav>
        <div className="app-bar__actions">
          {experience === "advanced" ? <span className="mode-pill">{t("settings.experience.advanced")}</span> : null}
          <nav className="app-utility-nav" aria-label={t("shell.utilities")}>
            {UTILITY_NAV.map((item) => <button
              className={item === page ? "utility-button utility-button--active" : "utility-button"}
              key={item}
              type="button"
              aria-current={item === primaryNavPage ? "page" : undefined}
              onClick={() => onNavigate(item)}
            ><span aria-hidden="true">{item === "settings" ? "⚙" : "?"}</span><span className="utility-button__label">{t(`shell.pages.${item}`)}</span></button>)}
          </nav>
          <LanguageSwitcher />
          <button className="utility-button utility-button--refresh" type="button" onClick={onRefresh} aria-label={t("app.refresh")} title={t("app.refresh")}>
            <span aria-hidden="true">↻</span><span className="utility-button__label">{t("app.refresh")}</span>
          </button>
        </div>
      </header>
      <div className="app-content" id="main-content">
        <div className="shell-feedback"><MutationFeedback result={mutationResult} onRefresh={onRefresh} onDismiss={clearMutationFeedback} /></div>
        {children}
      </div>
    </div>
  );
}
