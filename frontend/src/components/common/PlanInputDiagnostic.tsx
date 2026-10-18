import type { SharedPlanInputInspection } from "@generated/models";

/** Only the backend's bounded, redacted T4 projection is presented. */
export function PlanInputDiagnostic({ inspection }: { inspection: SharedPlanInputInspection | null }) {
  if (inspection?.status !== "rejected" || !inspection.diagnostic) return null;
  const diagnostic = inspection.diagnostic;
  return <div className="run-setup-edge-state run-setup-edge-state--error" role="alert" data-testid="plan-input-diagnostic">
    <strong>Plan inputs rejected: {diagnostic.code}</strong>
    {diagnostic.inputs.length > 0 ? <p>Inputs: {diagnostic.inputs.join(", ")}</p> : null}
    <p>{diagnostic.safe_next_action}</p>
    <small>Read-only inspection. No execution was started.</small>
  </div>;
}
