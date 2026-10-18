import { Component, type ErrorInfo, type ReactNode } from "react";

import { appDiagnosticStore } from "@/domain/diagnostics/appDiagnosticStore";
import { RecoveryPanel } from "@/components/common/RecoveryPanel";
import { i18n } from "@/i18n/setup";

interface Props {
  area: string;
  children: ReactNode;
}
interface State { failed: boolean; }

export class ComponentErrorBoundary extends Component<Props, State> {
  override state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    appDiagnosticStore.record({ source: `feature-render:${this.props.area}`, error: new Error(`${error.message}\n${info.componentStack ?? ""}`) });
  }

  private readonly retry = (): void => this.setState({ failed: false });

  override render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    return <section className="feature-recovery"><RecoveryPanel
      tone="warning"
      title={i18n.t("recovery.feature.title")}
      message={i18n.t("recovery.feature.message")}
      actions={<button className="button button--secondary" type="button" onClick={this.retry}>{i18n.t("recovery.tryAgain")}</button>}
    /></section>;
  }
}
