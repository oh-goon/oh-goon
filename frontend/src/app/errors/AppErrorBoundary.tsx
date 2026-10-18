import { Component, type ErrorInfo, type ReactNode } from "react";

import { appDiagnosticStore } from "@/domain/diagnostics/appDiagnosticStore";
import { RecoveryPanel } from "@/components/common/RecoveryPanel";
import { i18n } from "@/i18n/setup";

interface Props { children: ReactNode; }
interface State { failed: boolean; }

export class AppErrorBoundary extends Component<Props, State> {
  override state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    appDiagnosticStore.record({ source: "app-render", error: new Error(`${error.message}\n${info.componentStack ?? ""}`) });
  }

  override render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    return <main className="fatal-recovery"><RecoveryPanel
      tone="danger"
      live="assertive"
      title={i18n.t("recovery.fatal.title")}
      message={i18n.t("recovery.fatal.message")}
      actions={<button className="button button--primary" type="button" onClick={() => window.location.reload()}>{i18n.t("recovery.reloadApp")}</button>}
    /></main>;
  }
}
