import {
  operations,
  operationUrl,
  type OperationCall,
  type OperationName,
} from "@generated/operations";

export const API_ROOT = "/api";

type NavigationOperationName = {
  [Name in OperationName]: typeof operations[Name]["method"] extends "GET" ? Name : never;
}[OperationName];

export function operationHref<Name extends NavigationOperationName>(name: Name, call: OperationCall<Name>): string {
  if (operations[name].method !== "GET") throw new Error("Browser navigation requires a GET operation");
  return `${API_ROOT}${operationUrl(name, call)}`;
}
