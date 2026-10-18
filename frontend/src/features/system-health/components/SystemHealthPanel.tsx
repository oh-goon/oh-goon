import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";

import { SystemHealthStatus, ToolHealthReason, ToolHealthStatus as ToolHealthStatusValue } from "@generated/enums";
import type { SystemHealth, ToolHealth } from "@generated/models";
import { StatusBadge } from "@/components/common/StatusBadge";

interface SystemHealthPanelProps {
  health: SystemHealth | null;
}

type ToolHealthStatus = (typeof ToolHealthStatusValue)[keyof typeof ToolHealthStatusValue];

function statusLabel(t: TFunction, status: ToolHealthStatus): string {
  switch (status) {
    case ToolHealthStatusValue.READY: return t("systemHealth.status.READY");
    case ToolHealthStatusValue.MISSING: return t("systemHealth.status.MISSING");
    case ToolHealthStatusValue.BROKEN: return t("systemHealth.status.BROKEN");
    case ToolHealthStatusValue.OPTIONAL_MISSING: return t("systemHealth.status.OPTIONAL_MISSING");
  }
}

function reasonLabel(t: TFunction, tool: ToolHealth): string {
  switch (tool.reason_code) {
    case ToolHealthReason.TOOL_MISSING: return t("systemHealth.reason.TOOL_MISSING", { tool: tool.name });
    case ToolHealthReason.OPTIONAL_TOOL_MISSING: return t("systemHealth.reason.OPTIONAL_TOOL_MISSING", { tool: tool.name });
    case ToolHealthReason.TOOL_BROKEN: return t("systemHealth.reason.TOOL_BROKEN", { tool: tool.name });
    default: return tool.version ?? t("systemHealth.reason.NONE");
  }
}

export function SystemHealthPanel({ health }: SystemHealthPanelProps) {
  const { t } = useTranslation();
  if (health === null || health.status === SystemHealthStatus.READY) return null;
  const runtimeBlocked = !health.runtime_safety.effects_allowed;

  return (
    <section className="notice notice--warning" aria-labelledby="system-health-title">
      <div>
        <h2 id="system-health-title">{t("systemHealth.title")}</h2>
        <p>{runtimeBlocked ? t("systemHealth.runtimeDescription") : t("systemHealth.description")}</p>
      </div>
      {runtimeBlocked ? <div className="notice notice--warning"><strong>{t("systemHealth.runtimeTitle")}</strong><p>{t("systemHealth.runtimeBlocked")}</p><ul>{(health.runtime_safety.findings ?? []).map((finding) => <li key={`${finding.code}:${finding.message}`}><code>{finding.code}</code> · {finding.message}</li>)}</ul></div> : null}
      {health.background_failures.length > 0 ? <div className="notice notice--warning"><strong>{t("systemHealth.backgroundTitle")}</strong><ul>{health.background_failures.map((failure) => <li key={`${failure.code}:${failure.observed_at}`}><code>{failure.code}</code> · {failure.message}</li>)}</ul></div> : null}
      <div className="tool-list">
        {health.tools.map((tool) => (
          <div className="tool-row" key={tool.name}>
            <strong>{tool.name}</strong>
            <StatusBadge
              label={statusLabel(t, tool.status)}
              tone={tool.status === ToolHealthStatusValue.READY ? "success" : tool.required ? "danger" : "warning"}
            />
            <span>{reasonLabel(t, tool)}</span>
            {tool.detail ? <details><summary>{t("systemHealth.technicalDetail")}</summary><code>{tool.detail}</code></details> : null}
          </div>
        ))}
      </div>
    </section>
  );
}
