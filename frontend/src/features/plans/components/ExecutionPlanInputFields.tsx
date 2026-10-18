import { useTranslation } from "react-i18next";

import type { PlanInputDefinition } from "@generated/models";

export type ExecutionPlanInputValues = Record<string, string | null>;

export function initialExecutionPlanInputValues(
  definitions: readonly PlanInputDefinition[],
): ExecutionPlanInputValues {
  return Object.fromEntries(definitions.map((definition) => [
    definition.name,
    definition.default === null || definition.default === undefined ? null : String(definition.default),
  ]));
}

export function executionPlanInputs(
  values: Readonly<ExecutionPlanInputValues>,
): Record<string, string> {
  return Object.fromEntries(
    Object.entries(values).filter((entry): entry is [string, string] => entry[1] !== null),
  );
}

export function requiredExecutionPlanInputsComplete(
  definitions: readonly PlanInputDefinition[],
  values: Readonly<ExecutionPlanInputValues>,
): boolean {
  return definitions.every((definition) => {
    if (!definition.required) return true;
    const value = values[definition.name];
    return value !== null && value !== undefined && value !== "";
  });
}

export function ExecutionPlanInputFields({
  definitions,
  values,
  onChange,
}: {
  definitions: readonly PlanInputDefinition[];
  values: Readonly<ExecutionPlanInputValues>;
  onChange: (name: string, value: string | null) => void;
}) {
  const { t } = useTranslation();
  if (definitions.length === 0) return null;
  return <fieldset className="shared-plan-inputs">
    <legend>{t("sharedPlans.inputs.title")}</legend>
    <p className="muted">{t("sharedPlans.inputs.help")}</p>
    <div className="form-grid">
      {definitions.map((definition) => <ExecutionPlanInputField
        key={definition.name}
        definition={definition}
        value={values[definition.name] ?? null}
        onChange={(value) => onChange(definition.name, value)}
      />)}
    </div>
  </fieldset>;
}

function ExecutionPlanInputField({
  definition,
  value,
  onChange,
}: {
  definition: PlanInputDefinition;
  value: string | null;
  onChange: (value: string | null) => void;
}) {
  const { t } = useTranslation();
  const label = <><span>{definition.name}{definition.required ? " *" : ""}</span>{definition.description ? <small>{definition.description}</small> : null}</>;
  if (definition.input_type === "choice") {
    return <label>{label}<select value={value ?? ""} required={definition.required} onChange={(event) => onChange(event.target.value === "" && !definition.required ? null : event.target.value)}>
      {!definition.required ? <option value="">{t("sharedPlans.inputs.useNone")}</option> : <option value="">{t("sharedPlans.inputs.chooseValue")}</option>}
      {(definition.choices ?? []).map((choice) => <option key={choice} value={choice}>{choice}</option>)}
    </select></label>;
  }
  if (definition.input_type === "boolean") {
    return <label>{label}<select value={value ?? ""} required={definition.required} onChange={(event) => onChange(event.target.value === "" ? null : event.target.value)}>
      <option value="">{definition.required ? t("sharedPlans.inputs.chooseValue") : t("sharedPlans.inputs.useNone")}</option>
      <option value="true">true</option><option value="false">false</option>
    </select></label>;
  }
  return <label>{label}<input
    type={definition.input_type === "integer" ? "number" : "text"}
    value={value ?? ""}
    required={definition.required}
    min={definition.minimum ?? undefined}
    max={definition.maximum ?? undefined}
    pattern={definition.pattern ?? undefined}
    onChange={(event) => onChange(event.target.value === "" && !definition.required && definition.default === null ? null : event.target.value)}
  /></label>;
}
