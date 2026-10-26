import { useTranslation } from "react-i18next";

import type { PlanInputDefinition } from "@generated/models";

export type ExecutionPlanInputValues = Record<string, string | null | undefined>;

export function initialExecutionPlanInputValues(
  definitions: readonly PlanInputDefinition[],
): ExecutionPlanInputValues {
  return Object.fromEntries(definitions.map((definition) => [
    definition.name,
    undefined,
  ]));
}

export function executionPlanInputs(
  values: Readonly<ExecutionPlanInputValues>,
): Record<string, string | null> {
  return Object.fromEntries(
    Object.entries(values).filter((entry): entry is [string, string | null] => entry[1] !== undefined),
  );
}

export function requiredExecutionPlanInputsComplete(
  definitions: readonly PlanInputDefinition[],
  values: Readonly<ExecutionPlanInputValues>,
): boolean {
  return definitions.every((definition) => {
    if (!definition.required) return true;
    const value = values[definition.name] === undefined ? definition.default : values[definition.name];
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
  onChange: (name: string, value: string | null | undefined) => void;
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
        value={values[definition.name]}
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
  value: string | null | undefined;
  onChange: (value: string | null | undefined) => void;
}) {
  const { t } = useTranslation();
  const label = <><span>{definition.name}{definition.required ? " *" : ""}</span>{definition.description ? <small>{definition.description}</small> : null}</>;
  const defaultValue = definition.default === null || definition.default === undefined ? "" : String(definition.default);
  const displayedValue = value === undefined ? defaultValue : value ?? "";
  const source = <label><span>{t("sharedPlans.inputs.valueSource")}</span><select
    value={value === undefined ? "default" : value === null ? "null" : "explicit"}
    onChange={(event) => onChange(event.target.value === "default" ? undefined : event.target.value === "null" ? null : displayedValue)}
  >
    <option value="default">{t("sharedPlans.inputs.useDefault")}</option>
    <option value="explicit">{t("sharedPlans.inputs.explicitValue")}</option>
    <option value="null">{t("sharedPlans.inputs.explicitNull")}</option>
  </select></label>;
  if (definition.input_type === "choice") {
    return <div>{source}<label>{label}<select value={displayedValue} disabled={value === null} required={definition.required} onChange={(event) => onChange(event.target.value)}>
      <option value="">{t("sharedPlans.inputs.chooseValue")}</option>
      {(definition.choices ?? []).map((choice) => <option key={choice} value={choice}>{choice}</option>)}
    </select></label></div>;
  }
  if (definition.input_type === "boolean") {
    return <div>{source}<label>{label}<select value={displayedValue} disabled={value === null} required={definition.required} onChange={(event) => onChange(event.target.value)}>
      <option value="">{t("sharedPlans.inputs.chooseValue")}</option>
      <option value="true">true</option><option value="false">false</option>
    </select></label></div>;
  }
  return <div>{source}<label>{label}<input
    type={definition.input_type === "integer" ? "number" : "text"}
    value={displayedValue}
    disabled={value === null}
    required={definition.required}
    min={definition.minimum ?? undefined}
    max={definition.maximum ?? undefined}
    pattern={definition.pattern ?? undefined}
    onChange={(event) => onChange(event.target.value)}
  /></label></div>;
}
