import { readLocationHash, type AppPage } from "@/app/navigation";
import { scoutRouteFromHash, scoutRouteQuery, type ScoutRoute } from "@/features/scout/ScoutFeature";
import { ScoutFeature } from "@/features/scout/ScoutFeature";

interface Props {
  readonly navigate: (page: AppPage, query?: Readonly<Record<string, string>>) => void;
}

export function ScoutPage({ navigate }: Props) {
  const route = scoutRouteFromHash(readLocationHash());
  const onNavigate = (next: ScoutRoute) => navigate("scout", scoutRouteQuery(next));
  return <ScoutFeature route={route} onNavigate={onNavigate} onOpenRun={(runId) => navigate("runs", { run: runId })} />;
}
