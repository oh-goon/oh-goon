import { useTranslation } from "react-i18next";

import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";
import { RecoveryPanel } from "@/components/common/RecoveryPanel";

interface MutationFeedbackProps {
  result: WriteCommandResult<unknown> | null;
  onRefresh?: () => void;
  onRetry?: () => void;
  onDismiss?: () => void;
}

export function MutationFeedback({ result, onRefresh, onRetry, onDismiss }: MutationFeedbackProps) {
  const { t } = useTranslation();
  if (result === null) return null;

  if (result.outcome === "saved") {
    return <RecoveryPanel
      tone="success"
      title={t("mutationFeedback.savedTitle")}
      message={t("mutationFeedback.savedMessage")}
      actions={onDismiss ? <button className="text-button" type="button" onClick={onDismiss}>{t("common.dismiss")}</button> : undefined}
    />;
  }

  if (result.outcome === "stale") {
    return <RecoveryPanel
      title={t("mutationFeedback.staleTitle")}
      message={t("mutationFeedback.staleMessage")}
      actions={(onRefresh || onDismiss) ? <>{onRefresh ? <button className="button button--secondary" type="button" onClick={onRefresh}>{t("mutationFeedback.reload")}</button> : null}{onDismiss ? <button className="text-button" type="button" onClick={onDismiss}>{t("common.dismiss")}</button> : null}</> : undefined}
    />;
  }

  if (result.recovery === "refresh") {
    return <RecoveryPanel
      tone="danger"
      live="assertive"
      title={t("mutationFeedback.unknownTitle")}
      message={t("mutationFeedback.unknownMessage")}
      actions={(onRefresh || onDismiss) ? <>{onRefresh ? <button className="button button--secondary" type="button" onClick={onRefresh}>{t("mutationFeedback.reload")}</button> : null}{onDismiss ? <button className="text-button" type="button" onClick={onDismiss}>{t("common.dismiss")}</button> : null}</> : undefined}
    />;
  }

  const canRetry = result.recovery === "retry";
  return <RecoveryPanel
    tone="danger"
    live="assertive"
    title={t("mutationFeedback.errorTitle")}
    message={t(canRetry ? "mutationFeedback.recoverableMessage" : "mutationFeedback.errorMessage")}
    actions={(canRetry && onRetry) || onDismiss ? <>{canRetry && onRetry ? <button className="button button--secondary" type="button" onClick={onRetry}>{t("mutationFeedback.retry")}</button> : null}{onDismiss ? <button className="text-button" type="button" onClick={onDismiss}>{t("common.dismiss")}</button> : null}</> : undefined}
  />;
}
