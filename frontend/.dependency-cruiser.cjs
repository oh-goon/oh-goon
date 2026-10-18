const features = [
  "commands",
  "dependencies",
  "fleet",
  "game-tape",
  "history",
  "hockey",
  "league",
  "locations",
  "maintenance",
  "migration",
  "operations",
  "overview",
  "plans",
  "privacy",
  "recovery",
  "repositories",
  "schedules",
  "scout",
  "security",
  "system-health",
];

const featureIsolationRules = features.map((feature) => ({
  name: `feature-${feature}-isolated`,
  severity: "error",
  comment: "Features may not reach into another feature. App pages compose public feature roots.",
  from: { path: `^src/features/${feature}/` },
  to: {
    path: "^src/features/",
    pathNot: `^src/features/(${feature}/|[^/]+/[^/]+Feature\\.(ts|tsx)$)`,
  },
}));

module.exports = {
  forbidden: [
    {
      name: "no-circular",
      severity: "error",
      from: { path: "^src/" },
      to: { circular: true },
    },
    {
      name: "domain-is-framework-agnostic",
      severity: "error",
      from: { path: "^src/domain/" },
      to: { path: "^src/(app|components|features|i18n)/" },
    },
    {
      name: "common-components-have-no-business-dependencies",
      severity: "error",
      from: { path: "^src/components/common/" },
      to: { path: "^src/(app|domain|features)/" },
    },
    {
      name: "app-infrastructure-does-not-import-features",
      severity: "error",
      from: { path: "^src/app/(?!pages/)" },
      to: { path: "^src/features/" },
    },
    {
      name: "app-pages-use-feature-public-roots",
      severity: "error",
      from: { path: "^src/app/pages/" },
      to: { path: "^src/features/[^/]+/(components|hooks|model)(/|\\.)" },
    },
    {
      name: "feature-app-boundary",
      severity: "error",
      comment: "Features may use only the API provider and canonical read lifecycle from app infrastructure.",
      from: { path: "^src/features/" },
      to: {
        path: "^src/app/",
        pathNot: "^src/app/(ApiClientProvider\\.tsx|reads/useReadResource\\.ts)$",
      },
    },
    ...featureIsolationRules,
  ],
  options: {
    tsConfig: { fileName: "tsconfig.app.json" },
    doNotFollow: { path: "node_modules" },
    exclude: "(^|/)(dist|coverage|node_modules)(/|$)",
  },
};
