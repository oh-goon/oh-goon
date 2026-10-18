import { useTranslation } from "react-i18next";

import type { ScheduleFrequencyPreset } from "@/features/schedules/model";

export type ScheduleFrequencyChoice = ScheduleFrequencyPreset | "custom";

interface Props {
  frequency: ScheduleFrequencyChoice;
  customInterval: string;
  frequencyLabelKey: string;
  intervalLabelKey: string;
  onFrequencyChange: (value: ScheduleFrequencyChoice) => void;
  onCustomIntervalChange: (value: string) => void;
}

export function ScheduleFrequencyFields({
  frequency,
  customInterval,
  frequencyLabelKey,
  intervalLabelKey,
  onFrequencyChange,
  onCustomIntervalChange,
}: Props) {
  const { t } = useTranslation();
  return <>
    <label><span>{t(frequencyLabelKey)}</span><select required value={frequency} onChange={(event) => onFrequencyChange(event.target.value as ScheduleFrequencyChoice)}>
      <option value="hourly">{t("schedules.frequency.hourly")}</option>
      <option value="sixHours">{t("schedules.frequency.sixHours")}</option>
      <option value="daily">{t("schedules.frequency.daily")}</option>
      <option value="weekly">{t("schedules.frequency.weekly")}</option>
      <option value="custom">{t("schedules.frequency.custom")}</option>
    </select></label>
    {frequency === "custom" ? <label><span>{t(intervalLabelKey)}</span><input required type="number" min={1} max={10080} value={customInterval} onChange={(event) => onCustomIntervalChange(event.target.value)} /></label> : null}
  </>;
}
