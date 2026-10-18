import type { CausalityRevision } from "@/domain/concurrency/CausalityClock";

export type WriteCommandOutcome = "saved" | "stale" | "write-error";
export type WriteErrorRecovery = "retry" | "refresh" | "blocked";

export type WriteCommandResult<Value> =
  | { readonly outcome: "saved"; readonly value: Value; readonly causalityRevision: CausalityRevision }
  | { readonly outcome: "stale"; readonly diagnosticId: string }
  | { readonly outcome: "write-error"; readonly diagnosticId: string; readonly recovery: WriteErrorRecovery };
