import type { ReactNode } from "react";

interface RecoveryPanelProps {
  tone?: "warning" | "danger" | "scope" | "success";
  title: string;
  message: string;
  actions?: ReactNode;
  live?: "polite" | "assertive";
}

export function RecoveryPanel({ tone = "warning", title, message, actions, live = "polite" }: RecoveryPanelProps) {
  return (
    <section className={`notice notice--${tone} recovery-panel`} aria-live={live}>
      <div>
        <strong>{title}</strong>
        <p>{message}</p>
      </div>
      {actions ? <div className="recovery-panel__actions">{actions}</div> : null}
    </section>
  );
}
