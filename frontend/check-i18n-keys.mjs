import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.join(ROOT, "src");
const LOCALES = path.join(SRC, "i18n", "locales");

function flatten(value, prefix = "", result = new Set()) {
  for (const [key, child] of Object.entries(value)) {
    const next = prefix ? `${prefix}.${key}` : key;
    if (child && typeof child === "object" && !Array.isArray(child)) flatten(child, next, result);
    else result.add(next);
  }
  return result;
}

function walk(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) return walk(full);
    return /\.(ts|tsx)$/.test(entry.name) ? [full] : [];
  });
}

const localeNames = fs.readdirSync(LOCALES).filter((name) => fs.existsSync(path.join(LOCALES, name, "translation.json"))).sort();
if (localeNames.length < 2) throw new Error("Static i18n audit requires at least two locale files");
const localeKeys = new Map(localeNames.map((name) => [name, flatten(JSON.parse(fs.readFileSync(path.join(LOCALES, name, "translation.json"), "utf8")))]));
const referenceName = localeNames[0];
const reference = localeKeys.get(referenceName);
const failures = [];

// i18n parity consumes the committed generated contract. `make test` runs the
// independent generated-contract check separately, so i18n never needs to
// bootstrap Python/uv merely to validate frontend translation coverage.
const OPENAPI = path.join(ROOT, "..", "generated", "openapi", "openapi.json");
if (!fs.existsSync(OPENAPI)) throw new Error(`Generated OpenAPI contract is missing: ${OPENAPI}`);
const openapi = JSON.parse(fs.readFileSync(OPENAPI, "utf8"));
const reasonCodes = openapi.components?.schemas?.ReasonCode?.enum ?? [];
const explanationFields = ["what", "reason", "impact", "nextAction"];
if (!Array.isArray(reasonCodes) || reasonCodes.length === 0) {
  failures.push("OpenAPI exposes no ReasonCode enum for explanation parity");
} else {
  for (const code of reasonCodes) {
    for (const field of explanationFields) {
      const key = `explanations.${code}.${field}`;
      for (const [name, keys] of localeKeys) {
        if (!keys.has(key)) failures.push(`${name}: missing ReasonCode explanation key ${key}`);
      }
    }
  }
}

for (const [name, keys] of localeKeys) {
  for (const key of reference) if (!keys.has(key)) failures.push(`${name}: missing locale key ${key}`);
  for (const key of keys) if (!reference.has(key)) failures.push(`${name}: extra locale key ${key}`);
}

const literal = /(?:\bt|\bi18n\.t)\(\s*["']([^"']+)["']/g;
const template = /(?:\bt|\bi18n\.t)\(\s*`([^`]*)`/g;
for (const file of walk(SRC)) {
  const text = fs.readFileSync(file, "utf8");
  for (const match of text.matchAll(literal)) {
    const key = match[1];
    for (const [name, keys] of localeKeys) if (!keys.has(key)) failures.push(`${path.relative(ROOT, file)}: ${name} missing used key ${key}`);
  }
  for (const match of text.matchAll(template)) {
    const expression = match[1];
    const prefix = expression.split("${", 1)[0];
    if (!prefix) continue;
    for (const [name, keys] of localeKeys) {
      if (![...keys].some((key) => key.startsWith(prefix))) failures.push(`${path.relative(ROOT, file)}: ${name} has no key family for ${expression}`);
    }
  }
}

if (failures.length) {
  console.error("Static i18n parity audit failed:\n- " + [...new Set(failures)].join("\n- "));
  process.exit(1);
}
console.log(`Static i18n parity audit passed: ${reference.size} keys across ${localeNames.join(", ")}`);
