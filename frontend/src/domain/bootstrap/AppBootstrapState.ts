import type { FleetSnapshot } from "@generated/models";
import type { ReadFailure } from "@/domain/reads/ReadResource";

export type AppBootstrapState =
  | { readonly status: "boot-starting" }
  | { readonly status: "boot-checking-backend" }
  | { readonly status: "boot-loading-configuration" }
  | { readonly status: "boot-loading-fleet" }
  | { readonly status: "boot-configuration-required" }
  | { readonly status: "boot-ready"; readonly fleet: FleetSnapshot }
  | { readonly status: "boot-backend-unavailable"; readonly failure: ReadFailure }
  | { readonly status: "boot-recoverable-error"; readonly failure: ReadFailure };
