import { ApiError } from "@/domain/api/ApiError";

export function retainDataUnlessNotFound(error: unknown): boolean {
  return !(error instanceof ApiError && error.kind === "NOT_FOUND");
}
