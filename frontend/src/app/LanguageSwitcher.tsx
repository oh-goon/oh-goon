import { useTranslation } from "react-i18next";

import type { SupportedLanguage } from "@/i18n/resources";
import { saveLanguagePreference } from "@/app/preferences";

function currentLanguage(language: string | undefined): SupportedLanguage {
  return language?.startsWith("sv") ? "sv" : "en";
}

export function LanguageSwitcher() {
  const { i18n, t } = useTranslation();

  function changeLanguage(value: string) {
    const language: SupportedLanguage = value === "sv" ? "sv" : "en";
    saveLanguagePreference(language);
    void i18n.changeLanguage(language);
  }

  return (
    <label className="language-switcher">
      <span className="sr-only">{t("language.label")}</span>
      <select
        value={currentLanguage(i18n.resolvedLanguage)}
        onChange={(event) => changeLanguage(event.target.value)}
      >
        <option value="en">{t("language.english")}</option>
        <option value="sv">{t("language.swedish")}</option>
      </select>
    </label>
  );
}
