# Agent Communication Log: PatchOptic composable optics

Date: 2026-05-13
Orchestrator: Codex
Repository/branch: bAI-harness/main
Scope: Minimal internal structure for reusable typed optics.

## Task Summary

The task upgrades the PatchOptic-style policy layer from repeated workflow field
rules toward reusable composable optics. The slice is intentionally structural:
typed optic declarations, mechanical composition checks, workflow references by
optic id, and patch validation against visible lineage.

Out of scope: workflow schema rewrites, a new workflow engine, model calls,
workspace crawling, external PatchOptic runtime dependency, delete mutations,
and benchmark mapping changes.

## Source Inputs

- Prompt: `docs/dev/2026-05-13-patchoptic-composable-optics/orchestrator-prompt.md`
- Planning docs: `docs/PLAN.md`, `docs/RESOURCES.md`
- Relevant source files: `src/bai/core/policy.py`, `src/bai/artifacts/context.py`,
  `src/bai/artifacts/workflow.py`, `src/bai/execution/effects.py`
- Relevant tests: `tests/dev/test_package_layout.py`,
  `tests/dev/test_policy_gates.py`, `tests/dev/test_workflow_context.py`

## Role Assignments

### Worker

Assigned responsibility: identify the minimal implementation shape.

Expected output: module boundary, interfaces, and focused test plan.

Write scope: read-only assessment; orchestrator owns edits.

### Auditor

Assigned responsibility: check semantic alignment with PatchOptic principles.

Expected output: invariants to preserve and risks to test.

Review scope: policy, workflow, context, and mutation boundaries.

### Red Team

Assigned responsibility: identify technical debt and bypass risks.

Expected output: code-shape constraints and failure cases.

Risk scope: defensive coding, abstraction inflation, semantic drift, weak tests.

## Message Log

### 1. Orchestrator -> Roles

Summary: Asked three roles for independent assessments before implementation.

Full prompt or reference:
`docs/dev/2026-05-13-patchoptic-composable-optics/orchestrator-prompt.md`

### 2. Worker -> Orchestrator

Summary: Recommended a small deterministic contract layer, preferably
`src/bai/core/optics.py`, rather than embedding graph logic in workflow builders.

Evidence inspected: `src/bai/core/policy.py`, `src/bai/artifacts/context.py`,
`src/bai/artifacts/workflow.py`.

Claims:

- `PolicyEngine` should remain the deterministic gate.
- `ContextStore` and `WorkflowStore` are persistence boundaries, not optic graph
  engines.
- Optional artifact plumbing can come later after pure contract validation.

Proposed changes:

- Add frozen optic contracts, lineage requirements, workflow bindings, registry,
  and pure composition/projection/patch validators.
- Add focused tests in `tests/dev/test_optics.py`.

Open assumptions:

- No end-to-end Harness wiring is needed for this structural slice.

### 3. Auditor -> Orchestrator

Summary: Semantically aligned if optics remain inert typed policy metadata that
feed the existing deterministic gate rather than creating a parallel permission
path.

Evidence inspected: `src/bai/core/policy.py`, `src/bai/execution/effects.py`,
`src/bai/artifacts/approvals.py`, `src/bai/artifacts/context.py`.

Semantic alignment verdict: Acceptable for this slice.

Required corrections:

- Preserve declared visibility and mutation scope.
- Reject LLM-requested field expansion.
- Require lineage from visible evidence for writes.
- Keep test-command semantics honest as trusted direct execution.

Open questions:

- How future workflow artifacts should record resolved optic metadata.

### 4. Red Team -> Orchestrator

Summary: The largest risk is adding more `dict[str, Any]` convention without
typed frozen specs. A second risk is semantic drift between agent-output policy
and mutation execution.

Evidence inspected: loose `PolicyEngine` dictionaries, workflow node dictionaries,
mutation approval/preimage flow, no-dependency package test.

Critical risks:

- More nested dict contracts would remain safe only by convention.
- Delete mutation proposals were accepted at plan policy time even though the
  effect layer rejects them.
- Optics must not perform reads or duplicate path gating.

Medium/low debt:

- Path-resolution helpers are duplicated in existing modules.
- Workflow artifacts are still dict records, not typed policy objects.

Required repairs:

- Use frozen typed specs.
- Keep optics pure and read-free.
- Reject malformed specs deterministically.
- Reject delete proposals until a real delete execution path exists.

### 5. Orchestrator Decision

Decision: Implement only a pure `bai.core.optics` contract layer plus tests, and
fix the delete proposal drift in `PolicyEngine`.

Rationale: This satisfies the structural goal without changing Harness runtime
behavior or creating a second policy boundary.

Accepted recommendations:

- Add immutable optic/view/patch/registry types.
- Compose optics mechanically by typed view compatibility and field coverage.
- Require visible lineage evidence for write validation.
- Keep `PolicyEngine` as runtime enforcement.
- Add regression coverage for delete proposal rejection.

Rejected recommendations:

- No workflow artifact plumbing in this slice; it would imply an end-to-end
  semantics decision before schemas are ready.
- No schema version field yet; view type names and field compatibility are
  sufficient for the current minimal tests.
- No graph DSL or PatchOptic dependency.

Follow-up tasks:

- Decide how context/workflow artifacts should record resolved optic graphs.
- Decide whether view specs need explicit schema versions before broad schema
  migration.
- Consider consolidating duplicated path-resolution helpers separately.

## Implementation Record

Files changed:

- `src/bai/core/optics.py`
- `src/bai/core/policy.py`
- `tests/dev/test_optics.py`
- `tests/dev/test_policy_gates.py`
- `docs/dev/2026-05-13-patchoptic-composable-optics/architecture-note.md`
- `docs/dev/2026-05-13-patchoptic-composable-optics/communication-log.md`

Tests added/changed:

- Added `tests/dev/test_optics.py`.
- Added delete proposal regression in `tests/dev/test_policy_gates.py`.

Commands run:

- `python3 -m pytest tests/dev/test_optics.py tests/dev/test_policy_gates.py -q`
- `python3 -m pytest -q`

Results:

- Focused tests: 14 passed.
- Full suite: 196 passed.

## Final Validation

Validation command: `python3 -m pytest -q`

Result: 196 passed.

Known deviations:

- The new optics layer is not wired into Harness artifact generation yet.
- No explicit view schema version field is present.

Remaining non-scope work:

- Workflow schema migration to reference optic ids broadly.
- Optional context/workflow metadata for resolved optic graphs.
- Broader policy cleanup around duplicated path handling.
