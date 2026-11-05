import { describe, expect, it } from "vitest";

import { ContractValidationError } from "@generated/validators";
import {
  operationBody,
  operations,
  operationUrl,
} from "@generated/operations";

describe("generated API contracts", () => {
  it("accepts a valid request through the generated operation contract", () => {
    const name = operations.add_location.operationId;
    expect(operationBody(name, { body: { path: "/tmp/repositories" } })).toBe('{"path":"/tmp/repositories"}');
  });

  it("rejects missing required request fields through the generated operation contract", () => {
    const name = operations.add_location.operationId;
    expect(() => operationBody(name, { body: {} as never })).toThrow(ContractValidationError);
  });

  it("rejects undeclared request fields through the generated operation contract", () => {
    const name = operations.add_location.operationId;
    expect(() => operationBody(name, { body: { path: "/tmp/repositories", extra: true } as never })).toThrow(
      ContractValidationError,
    );
  });

  it("builds path and query parameters from generated operation metadata", () => {
    const name = operations.fleet_run_events.operationId;
    expect(operationUrl(name, { path: { fleet_run_id: "run / 1" }, query: { after: 7 } })).toBe(
      "/fleet/runs/run%20%2F%201/events?after=7",
    );
  });

  it("transports a scoped npm name through the generated path and ecosystem query", () => {
    expect(operationUrl(operations.dependency_repositories.operationId, {
      path: { package_name: "@scope/shared" }, query: { ecosystem: "npm", offset: 0, limit: 25 },
    })).toBe("/dependencies/%40scope%2Fshared/repositories?ecosystem=npm&offset=0&limit=25");
  });
});
