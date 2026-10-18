interface StatusBadgeProps {
  label: string;
  tone?: "success" | "info" | "warning" | "danger" | "neutral";
}

export function StatusBadge({ label, tone = "neutral" }: StatusBadgeProps) {
  return <span className={`status-badge status-badge--${tone}`}>{label}</span>;
}
