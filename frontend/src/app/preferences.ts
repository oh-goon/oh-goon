import type { SupportedLanguage } from "@/i18n/resources";

const LANGUAGE_KEY = "oh-goon.language";
const EXPERIENCE_KEY = "oh-goon.experience";

export type ExperienceMode = "standard" | "advanced";

export function loadLanguagePreference(): SupportedLanguage | null {
  const value = window.localStorage.getItem(LANGUAGE_KEY);
  return value === "en" || value === "sv" ? value : null;
}

export function saveLanguagePreference(language: SupportedLanguage): void {
  window.localStorage.setItem(LANGUAGE_KEY, language);
}

export function loadExperienceMode(): ExperienceMode {
  return window.localStorage.getItem(EXPERIENCE_KEY) === "advanced" ? "advanced" : "standard";
}

export function saveExperienceMode(mode: ExperienceMode): void {
  window.localStorage.setItem(EXPERIENCE_KEY, mode);
}
