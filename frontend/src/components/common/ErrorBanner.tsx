import { useTranslation } from "react-i18next";

interface ErrorBannerProps {
  title?: string;
  message: string;
}

export function ErrorBanner({ title, message }: ErrorBannerProps) {
  const { t } = useTranslation();
  return (
    <section className="notice notice--danger error-banner" role="alert">
      <div>
        <h2>{title ?? t("errors.title")}</h2>
        <p>{message}</p>
      </div>
    </section>
  );
}
