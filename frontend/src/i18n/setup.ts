import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import { resources, type SupportedLanguage } from "@/i18n/resources";
import { loadLanguagePreference } from "@/app/preferences";

export const DEFAULT_LANGUAGE: SupportedLanguage = "en";

export function detectedLanguage(): SupportedLanguage {
  const preferred = loadLanguagePreference();
  if (preferred !== null) return preferred;
  return navigator.language.toLowerCase().startsWith("sv") ? "sv" : DEFAULT_LANGUAGE;
}

void i18n.use(initReactI18next).init({
  resources,
  lng: detectedLanguage(),
  fallbackLng: DEFAULT_LANGUAGE,
  supportedLngs: Object.keys(resources),
  defaultNS: "translation",
  interpolation: { escapeValue: false },
});

export { i18n };
