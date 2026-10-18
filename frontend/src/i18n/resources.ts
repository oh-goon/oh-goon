import en from "@/i18n/locales/en/translation.json";
import sv from "@/i18n/locales/sv/translation.json";

export const resources = {
  en: { translation: en },
  sv: { translation: sv },
} as const;

export type SupportedLanguage = keyof typeof resources;
