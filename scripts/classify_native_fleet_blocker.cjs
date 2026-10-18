"use strict";

// M216: classify the *observed log record*, not the deployment authority.
// Only the original make certify owner can determine provisioning/certification.
// A diagnostic may not assemble one plausible blocker from unrelated output.
const fs = require("node:fs");

const marker = "OH_GOON_FLEET_PROVISIONING_BLOCKER=";
const logFile = process.argv[2];
if (!logFile || process.argv.length !== 3) process.exit(2);

let log;
try {
  log = fs.readFileSync(logFile, "utf8");
} catch {
  process.exit(2);
}

const marked = log.split(/\r?\n/).filter((line) => line.includes(marker));
if (marked.length !== 1 || !marked[0].startsWith(marker)) process.exit(1);

let blocker;
try {
  blocker = JSON.parse(marked[0].slice(marker.length));
} catch {
  process.exit(1);
}

if (blocker === null || typeof blocker !== "object" || Array.isArray(blocker)) process.exit(1);
const expected = {
  schema: "oh-goon.fleet-provisioning-blocker/v1",
  status: "BLOCKED",
  reason: "FLEET_ENFORCER_NOT_PROVISIONED",
  app_pass: false,
  certification_goon_started: false,
  authority: "NOT_ESTABLISHED",
  authenticated_replay: "NOT_AUTHORIZED",
  group_a_gate_a: "NOT_RUN",
};
for (const [name, value] of Object.entries(expected)) {
  if (!Object.hasOwn(blocker, name) || blocker[name] !== value) process.exit(1);
}
// Exit 0 means *only* that a coherent known blocker was observed in the log.
// It never means APP PASS, work admission, signer custody or release approval.
