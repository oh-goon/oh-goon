import { useTranslation } from "react-i18next";

import { LocationManager } from "@/features/locations/components/LocationManager";

interface Props {
  locations: string[];
  revision: string;
  actionsDisabled: boolean;
  onHelp: () => void;
}

export function FirstRunOnboarding({ locations, revision, actionsDisabled, onHelp }: Props) {
  const { t } = useTranslation();
  return <section className="onboarding-card">
    <div><span className="eyebrow">{t("onboarding.eyebrow")}</span><h1>{t("onboarding.title")}</h1><p>{t("onboarding.description")}</p></div>
    <ol className="onboarding-steps">
      <li className="onboarding-step onboarding-step--current"><span>1</span><div><strong>{t("onboarding.steps.folder.title")}</strong><small>{t("onboarding.steps.folder.message")}</small></div></li>
      <li className="onboarding-step"><span>2</span><div><strong>{t("onboarding.steps.check.title")}</strong><small>{t("onboarding.steps.check.message")}</small></div></li>
      <li className="onboarding-step"><span>3</span><div><strong>{t("onboarding.steps.automate.title")}</strong><small>{t("onboarding.steps.automate.message")}</small></div></li>
      <li className="onboarding-step"><span>4</span><div><strong>{t("onboarding.steps.review.title")}</strong><small>{t("onboarding.steps.review.message")}</small></div></li>
    </ol>
    <LocationManager locations={locations} revision={revision} actionsDisabled={actionsDisabled} />
    <button className="text-button" type="button" onClick={onHelp}>{t("onboarding.help")}</button>
  </section>;
}
