import { useTranslation } from "react-i18next";

export function ExclusionNotice({ excluded }: { excluded: number }) {
  const { t } = useTranslation();
  if (excluded <= 0) return null;
  return <div className="notice notice--warning" role="status">
    <strong>{t("exclusions.title", { count: excluded })}</strong><p>{t("exclusions.message")}</p>
  </div>;
}
