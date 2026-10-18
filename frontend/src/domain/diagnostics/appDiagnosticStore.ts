import { ApiError } from "@/domain/api/ApiError";
import { redactDiagnosticText } from "@/domain/diagnostics/diagnosticRedaction";
import type { CausalityRevision } from "@/domain/concurrency/CausalityClock";

const MAX_DIAGNOSTICS = 50;

type Listener = () => void;

export interface AppDiagnostic {
  readonly id: string;
  readonly source: string;
  readonly code: string;
  readonly message: string;
  readonly firstSeenAt: string;
  readonly lastSeenAt: string;
  readonly occurrences: number;
  readonly recoverable: boolean;
  readonly requestId: string | null;
  readonly scope: string | null;
  readonly causalityRevision: CausalityRevision | null;
}

interface DiagnosticInput {
  readonly source: string;
  readonly error: unknown;
  readonly scope?: string;
  readonly causalityRevision?: CausalityRevision;
}

class AppDiagnosticStore {
  private nextId = 0;
  private records: readonly AppDiagnostic[] = [];
  private readonly listeners = new Set<Listener>();

  readonly subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  readonly getSnapshot = (): readonly AppDiagnostic[] => this.records;

  record(input: DiagnosticInput): AppDiagnostic {
    const normalized = normalizeDiagnostic(input);
    const existing = this.records.find((item) => sameDiagnostic(item, normalized));
    const now = new Date().toISOString();
    if (existing !== undefined) {
      const updated = {
        ...existing,
        lastSeenAt: now,
        occurrences: existing.occurrences + 1,
        requestId: normalized.requestId,
        scope: normalized.scope,
        causalityRevision: normalized.causalityRevision,
      } satisfies AppDiagnostic;
      this.records = [updated, ...this.records.filter((item) => item.id !== existing.id)];
      this.emit();
      return updated;
    }

    const created = {
      id: `diag-${++this.nextId}`,
      source: normalized.source,
      code: normalized.code,
      message: normalized.message,
      firstSeenAt: now,
      lastSeenAt: now,
      occurrences: 1,
      recoverable: normalized.recoverable,
      requestId: normalized.requestId,
      scope: normalized.scope,
      causalityRevision: normalized.causalityRevision,
    } satisfies AppDiagnostic;
    this.records = [created, ...this.records].slice(0, MAX_DIAGNOSTICS);
    this.emit();
    return created;
  }

  clear(): void {
    if (this.records.length === 0) return;
    this.records = [];
    this.emit();
  }

  private emit(): void {
    this.listeners.forEach((listener) => listener());
  }
}

interface NormalizedDiagnostic {
  readonly source: string;
  readonly code: string;
  readonly message: string;
  readonly recoverable: boolean;
  readonly requestId: string | null;
  readonly scope: string | null;
  readonly causalityRevision: CausalityRevision | null;
}

function normalizeDiagnostic(input: DiagnosticInput): NormalizedDiagnostic {
  const error = input.error;
  const context = {
    source: input.source,
    scope: input.scope ?? null,
    causalityRevision: input.causalityRevision ?? null,
  } as const;
  if (error instanceof ApiError) {
    return {
      ...context,
      code: error.code,
      message: redactDiagnosticText(error.message),
      recoverable: error.retryable,
      requestId: error.requestId,
    };
  }
  if (error instanceof Error) {
    return {
      ...context,
      code: error.name || "APPLICATION_ERROR",
      message: redactDiagnosticText(error.message),
      recoverable: false,
      requestId: null,
    };
  }
  return {
    ...context,
    code: "UNKNOWN_ERROR",
    message: "An unknown application error was handled",
    recoverable: false,
    requestId: null,
  };
}

function sameDiagnostic(left: AppDiagnostic, right: NormalizedDiagnostic): boolean {
  return left.source === right.source && left.code === right.code && left.message === right.message;
}

export const appDiagnosticStore = new AppDiagnosticStore();
