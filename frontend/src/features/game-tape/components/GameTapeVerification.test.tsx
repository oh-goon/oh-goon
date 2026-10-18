import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { GameTape } from "@generated/models";
import { GameTapeVerification } from "@/features/game-tape/components/GameTapeVerification";

describe("GameTapeVerification", () => {
  it("joins producer-owned selected members to Oh-Goon execution outcomes", () => {
    const memberA = "tests/test_api.py::test_api";
    const memberB = "tests/test_cli.py::test_cli";
    const tape = {
      repositories: [{
        repository_execution_id: "repo-exec-1",
        repository: { repository_id: "repo-1", name: "api", path: "/repo" },
        status: "PASSED",
        fingerprint: { managed_input_count: 0, content_input_count: 0, tools: [], runtime_facts: [], host_environment_names: [] },
        steps: [],
        shard_lineage: [{
          shard_id: "verification-0",
          described_work_identity: `sha256:${"1".repeat(64)}`,
          physical_work_identity: `sha256:${"2".repeat(64)}`,
          refinement_depth: 0,
          status: "PASS",
          members: [memberA, memberB],
        }],
        verification_execution: {
          producer_version: "0.14.0",
          producer_artifact_identity: `sha256:${"3".repeat(64)}`,
          producer_implementation_identity: `sha256:${"e".repeat(64)}`,
          producer_implementation_provenance: {
            producer_schema: "hashmarks.producer-implementation-provenance.v1",
            authority: "non-authoritative-explanation",
            storage: "derived-not-persisted",
            identity_schema: "hashmarks.producer-implementation.v1",
            implementation_identity: `sha256:${"e".repeat(64)}`,
            input_count: 1,
            inputs: [{ path: "codemap/store.py", byte_count: 123, content_sha256: `sha256:${"f".repeat(64)}` }],
          },
          producer_contract_identity: `sha256:${"4".repeat(64)}`,
          repository_identity: `sha256:${"5".repeat(64)}:2`,
          revision_identity: `sha256:${"6".repeat(64)}`,
          selection_evidence_identity: `sha256:${"7".repeat(64)}`,
          producer_freshness_state: "PROVEN_FRESH",
          producer_provenance_identity: `sha256:${"7".repeat(64)}`,
          producer_repository_generation: 12,
          producer_identity_generation: 4,
          producer_evidence_hashes: [["ownership", `sha256:${"d".repeat(64)}`]],
          selected_membership_identity: `sha256:${"8".repeat(64)}`,
          selected_members: [memberA, memberB],
          actual_executed_membership_identity: `sha256:${"9".repeat(64)}`,
          actual_executed_members: [memberA, memberB],
          execution_plan_identity: `sha256:${"a".repeat(64)}`,
          execution_context_identity: `sha256:${"b".repeat(64)}`,
          execution_policy_identity: `sha256:${"c".repeat(64)}`,
          membership_complete: true,
          selection_state: "ADMITTED_FRESH",
          unproven_owner_members: [memberA, memberB],
        },
      }],
    } as unknown as GameTape;

    render(<GameTapeVerification tape={tape} />);

    expect(screen.getByRole("heading", { name: "Verification selection and execution" })).toBeTruthy();
    expect(screen.getByText(memberA)).toBeTruthy();
    expect(screen.getByText(memberB)).toBeTruthy();
    expect(screen.getAllByText("Execution result: PASS")).toHaveLength(2);
    expect(screen.getByText("ADMITTED_FRESH")).toBeTruthy();
    expect(screen.getByText("PROVEN_FRESH")).toBeTruthy();
    expect(screen.getByText("12")).toBeTruthy();
    expect(screen.getByText("Producer implementation provenance")).toBeTruthy();
    expect(screen.getByText("codemap/store.py")).toBeTruthy();
    expect(screen.getByText("non-authoritative-explanation")).toBeTruthy();
    expect(screen.getByText("derived-not-persisted")).toBeTruthy();
  });
});
