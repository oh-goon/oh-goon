interface SummaryCardProps {
  label: string;
  value: number;
  hint: string;
  tone?: "neutral" | "success" | "warning";
}

export function SummaryCard({ label, value, hint, tone = "neutral" }: SummaryCardProps) {
  return (
    <article className={`summary-card summary-card--${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{hint}</small>
    </article>
  );
}
