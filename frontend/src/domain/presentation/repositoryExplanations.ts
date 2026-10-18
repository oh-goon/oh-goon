import type { ReasonCode } from "@generated/models";

export type ExplanationField = "what" | "reason" | "impact" | "nextAction";
export type ExplanationTranslationKey = `explanations.${ReasonCode}.${ExplanationField}`;

export interface ExplanationKeys {
  what: ExplanationTranslationKey;
  reason: ExplanationTranslationKey;
  impact: ExplanationTranslationKey;
  nextAction: ExplanationTranslationKey;
}

function explanationKey(code: ReasonCode, field: ExplanationField): ExplanationTranslationKey {
  return `explanations.${code}.${field}`;
}

export function explanationKeys(code: ReasonCode): ExplanationKeys {
  return {
    what: explanationKey(code, "what"),
    reason: explanationKey(code, "reason"),
    impact: explanationKey(code, "impact"),
    nextAction: explanationKey(code, "nextAction"),
  };
}
