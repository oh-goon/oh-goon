from __future__ import annotations

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS locations (
    path TEXT PRIMARY KEY,
    position INTEGER NOT NULL UNIQUE,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_lease (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    lease_token TEXT NOT NULL UNIQUE,
    owner_session_id TEXT NOT NULL,
    owner_pid INTEGER NOT NULL,
    owner_identity TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('DIRECT', 'FLEET')),
    fleet_run_id TEXT,
    acquired_at TEXT NOT NULL,
    cancel_requested_at TEXT,
    CHECK (
        (kind = 'DIRECT' AND fleet_run_id IS NULL)
        OR (kind = 'FLEET' AND fleet_run_id IS NOT NULL)
    )
);


CREATE TABLE IF NOT EXISTS executor_placement_queue (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    fleet_run_id TEXT NOT NULL UNIQUE,
    scheduling_scope_id TEXT NOT NULL DEFAULT 'local-default',
    admission_domain_id TEXT NOT NULL DEFAULT 'default',
    requested_priority TEXT NOT NULL DEFAULT 'NORMAL' CHECK (requested_priority IN ('CRITICAL', 'HIGH', 'NORMAL', 'LOW', 'BACKGROUND')),
    admitted_priority TEXT NOT NULL DEFAULT 'NORMAL' CHECK (admitted_priority IN ('CRITICAL', 'HIGH', 'NORMAL', 'LOW', 'BACKGROUND')),
    priority_scope TEXT NOT NULL DEFAULT 'DOMAIN' CHECK (priority_scope IN ('DOMAIN', 'SCHEDULING_SCOPE')),
    priority_decision_reason TEXT NOT NULL DEFAULT 'DEFAULT_OR_AUTHORIZED',
    scheduling_revision INTEGER NOT NULL DEFAULT 0 CHECK (scheduling_revision >= 0),
    scheduling_policy_revision TEXT NOT NULL DEFAULT 'local-v1',
    priority_policy_revision TEXT NOT NULL DEFAULT 'local-v1',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    owner_session_id TEXT NOT NULL,
    owner_pid INTEGER NOT NULL,
    owner_identity TEXT NOT NULL,
    cancellation_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancellation_requested IN (0, 1)),
    eligibility_ready INTEGER NOT NULL DEFAULT 0 CHECK (eligibility_ready IN (0, 1)),
    eligibility_revision INTEGER NOT NULL DEFAULT 0 CHECK (eligibility_revision >= 0)
);
CREATE INDEX IF NOT EXISTS executor_placement_queue_order
    ON executor_placement_queue(
        scheduling_scope_id,
        CASE admitted_priority
            WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'NORMAL' THEN 2
            WHEN 'LOW' THEN 3 ELSE 4 END,
        sequence, fleet_run_id
    );

CREATE TABLE IF NOT EXISTS run_scheduling_receipts (
    fleet_run_id TEXT PRIMARY KEY,
    scheduling_scope_id TEXT NOT NULL,
    admission_domain_id TEXT NOT NULL,
    requested_priority TEXT NOT NULL CHECK (requested_priority IN ('CRITICAL', 'HIGH', 'NORMAL', 'LOW', 'BACKGROUND')),
    admitted_priority TEXT NOT NULL CHECK (admitted_priority IN ('CRITICAL', 'HIGH', 'NORMAL', 'LOW', 'BACKGROUND')),
    priority_scope TEXT NOT NULL CHECK (priority_scope IN ('DOMAIN', 'SCHEDULING_SCOPE')),
    priority_decision_reason TEXT NOT NULL,
    queue_seq INTEGER NOT NULL CHECK (queue_seq >= 1),
    scheduling_revision INTEGER NOT NULL CHECK (scheduling_revision >= 0),
    scheduling_policy_revision TEXT NOT NULL,
    priority_policy_revision TEXT NOT NULL,
    created_at TEXT NOT NULL,
    admitted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS run_scheduling_receipts_scope_order
    ON run_scheduling_receipts(scheduling_scope_id, queue_seq, fleet_run_id);

CREATE TABLE IF NOT EXISTS executor_capacity_reservations (
    fleet_run_id TEXT PRIMARY KEY,
    reservation_id TEXT NOT NULL UNIQUE,
    executor_id TEXT NOT NULL,
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json) AND json_type(evidence_json) = 'object')
);
CREATE INDEX IF NOT EXISTS executor_capacity_reservation_executor
    ON executor_capacity_reservations(executor_id, fleet_run_id);

CREATE TABLE IF NOT EXISTS executor_configured_generation (
    executor_id TEXT PRIMARY KEY,
    generation INTEGER NOT NULL CHECK (generation >= 1)
);

CREATE TABLE IF NOT EXISTS executor_operational_state (
    executor_id TEXT PRIMARY KEY,
    admission_mode TEXT NOT NULL CHECK (admission_mode IN ('active', 'draining')),
    trust_state TEXT NOT NULL CHECK (trust_state IN ('trusted', 'revoked')),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    trust_epoch INTEGER NOT NULL CHECK (trust_epoch >= 0)
);

CREATE TABLE IF NOT EXISTS journal_entries (
    revision INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('OPERATION', 'RUN_EVENT', 'FLEET_OPERATION', 'EFFECT_RECONCILIATION', 'EFFECT_ACKNOWLEDGEMENT', 'CAMPAIGN')),
    report_json TEXT,
    event_json TEXT,
    effect_json TEXT,
    committed_at TEXT NOT NULL,
    previous_hash TEXT NOT NULL CHECK (length(previous_hash) = 64),
    fact_hash TEXT NOT NULL UNIQUE CHECK (length(fact_hash) = 64),
    CHECK (
        (kind = 'OPERATION' AND report_json IS NOT NULL AND event_json IS NULL AND effect_json IS NULL)
        OR (kind = 'RUN_EVENT' AND report_json IS NULL AND event_json IS NOT NULL AND effect_json IS NULL)
        OR (kind = 'FLEET_OPERATION' AND report_json IS NOT NULL AND event_json IS NOT NULL AND effect_json IS NULL)
        OR (kind = 'EFFECT_RECONCILIATION' AND report_json IS NULL AND event_json IS NULL AND effect_json IS NOT NULL)
        OR (kind = 'CAMPAIGN' AND report_json IS NULL AND event_json IS NULL AND effect_json IS NOT NULL)
        OR (kind = 'EFFECT_ACKNOWLEDGEMENT' AND report_json IS NULL AND event_json IS NULL AND effect_json IS NOT NULL)
    )
);


CREATE TABLE IF NOT EXISTS repository_effect_attempts (
    operation_id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL,
    repository_path TEXT NOT NULL,
    capability_json TEXT NOT NULL,
    target_json TEXT,
    owner_kind TEXT NOT NULL CHECK (owner_kind IN ('DIRECT', 'FLEET')),
    fleet_run_id TEXT,
    effect_boundary TEXT NOT NULL CHECK (effect_boundary IN ('EXTERNAL', 'CONTAINED')),
    execution_input_id TEXT,
    status TEXT NOT NULL CHECK (status IN (
        'CLAIMED', 'EFFECT_STARTED', 'OUTCOME_UNKNOWN', 'PARTIALLY_APPLIED', 'INDETERMINATE'
    )),
    baseline_json TEXT NOT NULL,
    reconciliation_json TEXT,
    record_version INTEGER NOT NULL DEFAULT 0 CHECK (record_version >= 0),
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    note TEXT,
    CHECK (
        (owner_kind = 'DIRECT' AND fleet_run_id IS NULL)
        OR (owner_kind = 'FLEET' AND fleet_run_id IS NOT NULL)
    ),
    CHECK (
        (status IN ('PARTIALLY_APPLIED', 'INDETERMINATE') AND reconciliation_json IS NOT NULL)
        OR (status IN ('CLAIMED', 'EFFECT_STARTED', 'OUTCOME_UNKNOWN') AND reconciliation_json IS NULL)
    ),
    CHECK (
        (status = 'OUTCOME_UNKNOWN' AND note IS NOT NULL AND length(trim(note)) > 0)
        OR (status <> 'OUTCOME_UNKNOWN' AND note IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS effect_attempt_repository_order
    ON repository_effect_attempts(repository_id, started_at DESC);
CREATE INDEX IF NOT EXISTS effect_attempt_fleet_run
    ON repository_effect_attempts(fleet_run_id, started_at) WHERE owner_kind = 'FLEET';

CREATE TABLE IF NOT EXISTS schedule_definitions (
    schedule_id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    repository_ids_json TEXT NOT NULL CHECK (json_valid(repository_ids_json) AND json_type(repository_ids_json) = 'array' AND json_array_length(repository_ids_json) > 0),
    repository_instance_ids_json TEXT NOT NULL CHECK (json_valid(repository_instance_ids_json) AND json_type(repository_instance_ids_json) = 'object'),
    target_json TEXT NOT NULL CHECK (json_valid(target_json) AND json_type(target_json) = 'object'),
    interval_minutes INTEGER NOT NULL CHECK (interval_minutes BETWEEN 1 AND 10080),
    state TEXT NOT NULL CHECK (state IN ('active', 'paused')),
    record_version INTEGER NOT NULL DEFAULT 0 CHECK (record_version >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedule_executions (
    schedule_run_id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL,
    intent_json TEXT NOT NULL CHECK (json_valid(intent_json) AND json_type(intent_json) = 'object'),
    repository_runs_json TEXT NOT NULL CHECK (json_valid(repository_runs_json) AND json_type(repository_runs_json) = 'array' AND json_array_length(repository_runs_json) > 0),
    status TEXT NOT NULL CHECK (status IN (
        'RUNNING', 'COMPLETED', 'ATTENTION_REQUIRED', 'FAILED', 'CANCELLED'
    )),
    attention_json TEXT NOT NULL CHECK (json_valid(attention_json) AND json_type(attention_json) = 'array'),
    failure_json TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    record_version INTEGER NOT NULL DEFAULT 0 CHECK (record_version >= 0),
    CHECK (
        (status = 'RUNNING' AND finished_at IS NULL)
        OR (status IN ('COMPLETED', 'ATTENTION_REQUIRED', 'FAILED', 'CANCELLED') AND finished_at IS NOT NULL)
    ),
    CHECK (
        (status = 'FAILED' AND failure_json IS NOT NULL)
        OR (status <> 'FAILED' AND failure_json IS NULL)
    ),
    CHECK (status <> 'COMPLETED' OR json_array_length(attention_json) = 0),
    CHECK (status <> 'ATTENTION_REQUIRED' OR json_array_length(attention_json) > 0)
);
CREATE INDEX IF NOT EXISTS schedule_execution_order
    ON schedule_executions(schedule_id, started_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS schedule_one_active_execution
    ON schedule_executions(schedule_id) WHERE status = 'RUNNING';


CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL,
    base_revision TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('READ_SNAPSHOT', 'WORKTREE')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspace_state (
    workspace_id TEXT PRIMARY KEY
        REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN (
        'REQUESTED', 'ALLOCATED', 'READY', 'IN_USE',
        'FINALIZED', 'RELEASED', 'FAILED'
    )),
    state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    current_lease_id TEXT,
    current_checkpoint_id TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspace_read_snapshots (
    workspace_id TEXT PRIMARY KEY
        REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
    entry_count INTEGER NOT NULL CHECK (entry_count >= 0),
    materialized_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspace_worktrees (
    workspace_id TEXT PRIMARY KEY
        REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    base_revision TEXT NOT NULL,
    git_marker_sha256 TEXT NOT NULL CHECK (length(git_marker_sha256) = 64 AND git_marker_sha256 NOT GLOB '*[^0-9a-f]*'),
    materialized_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL
        REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    parent_checkpoint_id TEXT
        REFERENCES candidate_checkpoints(checkpoint_id),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
    entry_count INTEGER NOT NULL CHECK (entry_count >= 0),
    changed_paths_json TEXT NOT NULL CHECK (json_valid(changed_paths_json) AND json_type(changed_paths_json) = 'array'),
    attempt_id TEXT,
    assignment_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((attempt_id IS NULL) = (assignment_id IS NULL))
);
CREATE INDEX IF NOT EXISTS candidate_checkpoints_workspace_order
    ON candidate_checkpoints(workspace_id, created_at, checkpoint_id);

CREATE TABLE IF NOT EXISTS execution_assignments (
    assignment_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    input_checkpoint_id TEXT NOT NULL REFERENCES candidate_checkpoints(checkpoint_id),
    assignment_fingerprint TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json) AND json_type(payload_json) = 'object'),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS execution_assignments_workspace_checkpoint
    ON execution_assignments(workspace_id, input_checkpoint_id, created_at);

CREATE TABLE IF NOT EXISTS workspace_leases (
    lease_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    attempt_id TEXT NOT NULL,
    assignment_id TEXT NOT NULL REFERENCES execution_assignments(assignment_id),
    assignment_fingerprint TEXT NOT NULL,
    executor_id TEXT NOT NULL,
    executor_generation INTEGER NOT NULL CHECK (executor_generation >= 1),
    input_checkpoint_id TEXT NOT NULL REFERENCES candidate_checkpoints(checkpoint_id),
    lease_generation INTEGER NOT NULL CHECK (lease_generation >= 1),
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'RELEASED', 'EXPIRED', 'QUARANTINED')),
    writer_authority_kind TEXT CHECK (writer_authority_kind IN ('cgroup', 'process_group', 'remote_backend')),
    writer_authority_id TEXT,
    fence_evidence_json TEXT CHECK (fence_evidence_json IS NULL OR (json_valid(fence_evidence_json) AND json_type(fence_evidence_json) = 'object')),
    finished_at TEXT,
    CHECK ((writer_authority_kind IS NULL) = (writer_authority_id IS NULL)),
    CHECK ((status = 'ACTIVE' AND finished_at IS NULL) OR (status <> 'ACTIVE' AND finished_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS workspace_one_active_mutation_lease
    ON workspace_leases(workspace_id) WHERE status = 'ACTIVE';
CREATE UNIQUE INDEX IF NOT EXISTS workspace_lease_generation_unique
    ON workspace_leases(workspace_id, lease_generation);

CREATE TABLE IF NOT EXISTS execution_materializations (
    materialization_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL,
    assignment_id TEXT NOT NULL REFERENCES execution_assignments(assignment_id),
    assignment_fingerprint TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    input_checkpoint_id TEXT NOT NULL REFERENCES candidate_checkpoints(checkpoint_id),
    lease_id TEXT NOT NULL REFERENCES workspace_leases(lease_id),
    lease_generation INTEGER NOT NULL CHECK (lease_generation >= 1),
    executor_id TEXT NOT NULL,
    executor_generation INTEGER NOT NULL CHECK (executor_generation >= 1),
    resolved_fingerprint TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json) AND json_type(payload_json) = 'object'),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(assignment_id, lease_id)
);
CREATE INDEX IF NOT EXISTS execution_materializations_workspace_checkpoint
    ON execution_materializations(workspace_id, input_checkpoint_id, created_at);

CREATE TABLE IF NOT EXISTS execution_evidence_acceptances (
    acceptance_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL,
    assignment_id TEXT NOT NULL REFERENCES execution_assignments(assignment_id),
    assignment_fingerprint TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    input_checkpoint_id TEXT NOT NULL REFERENCES candidate_checkpoints(checkpoint_id),
    output_checkpoint_id TEXT NOT NULL REFERENCES candidate_checkpoints(checkpoint_id),
    lease_id TEXT NOT NULL REFERENCES workspace_leases(lease_id),
    lease_generation INTEGER NOT NULL CHECK (lease_generation >= 1),
    executor_id TEXT NOT NULL,
    executor_generation INTEGER NOT NULL CHECK (executor_generation >= 1),
    materialization_id TEXT NOT NULL REFERENCES execution_materializations(materialization_id),
    materialization_fingerprint TEXT NOT NULL,
    result_identity TEXT NOT NULL,
    acceptance_fingerprint TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json) AND json_type(payload_json) = 'object'),
    accepted_at TEXT NOT NULL,
    UNIQUE(materialization_id)
);
CREATE INDEX IF NOT EXISTS execution_evidence_acceptances_workspace_output
    ON execution_evidence_acceptances(workspace_id, output_checkpoint_id, accepted_at);

CREATE TABLE IF NOT EXISTS observation_index_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    journal_revision INTEGER NOT NULL CHECK (journal_revision >= 0)
);
INSERT OR IGNORE INTO observation_index_metadata(singleton, journal_revision) VALUES (1, 0);

CREATE TABLE IF NOT EXISTS repository_observation_heads (
    repository_id TEXT PRIMARY KEY,
    repository_name TEXT NOT NULL,
    repository_path TEXT NOT NULL,
    repository_state_id TEXT NOT NULL,
    head_sha TEXT,
    pyproject_sha256 TEXT,
    uv_lock_sha256 TEXT,
    fleet_run_id TEXT,
    operation_id TEXT NOT NULL,
    observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dependency_observation_snapshots (
    repository_id TEXT NOT NULL,
    ecosystem TEXT NOT NULL CHECK (ecosystem IN ('pypi')),
    snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
    fleet_run_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    producer_goon TEXT,
    step_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    dependency_count INTEGER NOT NULL CHECK (dependency_count >= 0),
    PRIMARY KEY(repository_id, ecosystem)
);

CREATE TABLE IF NOT EXISTS dependency_observations (
    repository_id TEXT NOT NULL,
    ecosystem TEXT NOT NULL CHECK (ecosystem IN ('pypi')),
    package_name TEXT NOT NULL,
    node_id TEXT NOT NULL,
    resolved_version TEXT NOT NULL,
    resolved_source TEXT NOT NULL,
    package_source TEXT NOT NULL,
    contexts_json TEXT NOT NULL CHECK (json_valid(contexts_json)),
    fleet_run_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    producer_goon TEXT,
    step_id TEXT NOT NULL,
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS dependency_observation_package
    ON dependency_observations(ecosystem, package_name, resolved_version, repository_id);
CREATE INDEX IF NOT EXISTS dependency_observation_repository
    ON dependency_observations(repository_id, package_name);

CREATE TABLE IF NOT EXISTS dependency_target_materializations (
    query_id TEXT PRIMARY KEY,
    selected_at TEXT NOT NULL,
    filter_json TEXT NOT NULL CHECK (json_valid(filter_json) AND json_type(filter_json) = 'object'),
    repository_ids_json TEXT NOT NULL CHECK (json_valid(repository_ids_json) AND json_type(repository_ids_json) = 'array'),
    repository_instance_ids_json TEXT NOT NULL CHECK (json_valid(repository_instance_ids_json) AND json_type(repository_instance_ids_json) = 'object'),
    total INTEGER NOT NULL CHECK (total >= 0 AND total <= 5000),
    CHECK (json_array_length(repository_ids_json) = total)
);
CREATE INDEX IF NOT EXISTS dependency_target_materialization_time
    ON dependency_target_materializations(selected_at DESC);
"""
