import { useTranslation } from "react-i18next";

import type { PrivacyResponse } from "@generated/models";

interface PrivacyNoticeProps {
  privacy: PrivacyResponse | null;
}

export function PrivacyNotice({ privacy }: PrivacyNoticeProps) {
  const { t } = useTranslation();
  if (privacy === null) return null;

  return (
    <section className="privacy-notice" aria-labelledby="privacy-title">
      <div>
        <span className="eyebrow">{t("privacy.title")}</span>
        <p id="privacy-title">{t("privacy.summary")}</p>
        <small>{t("privacy.networkNote")}</small>
      </div>
      <div className="privacy-badges" role="group" aria-label={t("privacy.title")}>
        <span>{t("privacy.loopback")}</span>
        <span>{t("privacy.localPrefs")}</span>
        <span>{t("privacy.policyStorage")}</span>
        <span>{t("privacy.noTelemetry")}</span>
        <span>{t("privacy.noUpload")}</span>
      </div>
    </section>
  );
}
