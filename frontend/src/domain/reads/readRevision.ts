import { canonicalIdentity, canonicalSerialize } from "@/domain/identity/canonicalValue";
import type { FleetRunHistoryPage, FleetSnapshot } from "@generated/models";

export function fleetSnapshotRevision(snapshot: FleetSnapshot): string {
  const provenance = snapshot.provenance;
  return `snapshot:${canonicalIdentity({ snapshotId: provenance.snapshot_id, durableRevision: provenance.durable_revision, journalRevision: provenance.journal_revision })}`;
}

export function historyPageRevision(page: FleetRunHistoryPage): string {
  return `journal:${page.journal_revision}`;
}

export function contentRevision(value: unknown): string {
  const serialized = canonicalSerialize(value);
  let hash = 2166136261;
  for (let index = 0; index < serialized.length; index += 1) {
    hash ^= serialized.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `content:${(hash >>> 0).toString(16).padStart(8, "0")}`;
}
