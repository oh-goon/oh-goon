export type HockeyLane = "preGame" | "game" | "egress" | "postGame";

export type HockeyPersonaId =
  | "enforcer"
  | "equipmentManager"
  | "warmUp"
  | "redTeam"
  | "scout"
  | "trainer"
  | "coach"
  | "linesman"
  | "penaltyBox"
  | "goalie"
  | "zamboni"
  | "mascot";

export interface HockeyPersona {
  id: HockeyPersonaId;
  lane: HockeyLane;
  optional: boolean;
}

export const HOCKEY_TEAM: readonly HockeyPersona[] = [
  { id: "enforcer", lane: "preGame", optional: false },
  { id: "equipmentManager", lane: "preGame", optional: false },
  { id: "warmUp", lane: "preGame", optional: false },
  { id: "redTeam", lane: "preGame", optional: true },
  { id: "scout", lane: "game", optional: false },
  { id: "trainer", lane: "game", optional: true },
  { id: "coach", lane: "game", optional: true },
  { id: "linesman", lane: "game", optional: false },
  { id: "penaltyBox", lane: "game", optional: true },
  { id: "goalie", lane: "egress", optional: true },
  { id: "zamboni", lane: "postGame", optional: false },
  { id: "mascot", lane: "postGame", optional: true },
] as const;

export const CLEAN_PASS_MASCOT = String.raw`      \o/   CLEAN ICE
       |   OH-GOON
      / \    ====>  🏒
  [ verified final whistle ]`;
