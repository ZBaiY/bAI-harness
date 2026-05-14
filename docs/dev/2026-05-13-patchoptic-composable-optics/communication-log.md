# Agent Communication Log: Optic Category Agent Workflow

## Orchestrator / Worker

Task source:
- `orchestrator-prompt.md`
- `optic-category-agent-workflow.md`
- `source-workflow-skills.md`

Implementation decision:
- Added a declarative `bai.optic_category` module rather than extending the
  field-projection `bai.core.optics` module.
- Kept the layer structural only: no agents, command execution, workspace
  crawling, persistence, scheduler integration, or mutation behavior.

Files added:
- `src/bai/optic_category.py`
- `tests/dev/test_optic_category.py`
- `tests/e2e/test_bug_fix_structural_loop_optic.py`

Validation:
- `pytest tests/dev/test_optics.py tests/dev/test_optic_category.py tests/e2e/test_bug_fix_structural_loop_optic.py -q`
- `pytest -q`

Result:
- `BugFixStructuralLoopOptic` is exposed by registry builders.
- Primitive role optics declare input types, output types, read projections,
  write surfaces, lineage requirements, and optional outputs.
- Composition validation fails closed for missing or mismatched handoffs,
  undeclared inputs, missing lineage, and writes outside declared surfaces.

## Auditor Review

Verdict:
- `DRIFTED / NOT ALIGNED`

Blocking findings:
- Intermediate artifacts could be added to `CompositionSpec.input_types`,
  bypassing required upstream handoffs.
- `noop_inputs` could satisfy any declared role input, including required
  artifacts such as `AuditReport`.
- Write-surface validation accepted traversal-shaped targets.

Decision:
- All three findings were classified as must-fix-now because they weaken typed
  handoff preservation, lineage semantics, and bounded write surfaces.

## Red Team Review

Verdict:
- `REPAIR BEFORE ACCEPTANCE`

Critical debt:
- `PatcherOptic` symbolic write surface `<repair-plan-authorized-files>` acted
  as an authorizing wildcard.
- Absolute and traversal-like write targets could match declared surfaces.
- Internal artifact handoffs could be bypassed by overdeclaring composition
  inputs.

Medium/low debt:
- `noop_inputs` remains a declarative no-patch marker, not execution evidence.
- `core.optics` and `optic_category` are separate optics surfaces; this is
  accepted for now because `core.optics` covers field projection and
  `optic_category` covers role artifact composition.
- `conditional_write_surface` remains declarative and caller-bound until an
  execution integration exists.

Decision:
- Critical debt was must-fix-now.
- Medium/low debt was accepted as declarative-only boundary or future cleanup.

## Repairs Applied

Files changed:
- `src/bai/optic_category.py`
- `tests/dev/test_optic_category.py`

Repairs:
- Reject composition boundary inputs that are produced by source optics.
- Restrict `noop_inputs` to artifact types listed as composition optional
  outputs.
- Reject required/optional composition output overlap.
- Reject absolute write targets and any `.` / `..` / empty path segments.
- Treat symbolic write placeholders other than `<domain>` as non-authorizing.

Tests added:
- Patcher arbitrary write denial.
- Auditor traversal and absolute target denial.
- Intermediate artifact externalization denial.
- Non-optional no-op input denial.
- Required/optional composition output disjointness.

Validation:
- `pytest tests/dev/test_optic_category.py -q` -> `10 passed`
- `pytest tests/dev/test_optics.py tests/dev/test_optic_category.py tests/e2e/test_bug_fix_structural_loop_optic.py -q` -> `22 passed`
- `pytest -q` -> `210 passed`

## Bug-Fix Workflow E2E Test Writer

Test design:
- Added a concrete calculator bug workspace in
  `tests/e2e/test_optic_category_bugfix_workflow.py`.
- Starts from real CLI calls: `workspace add`, `workspace allow-test`, and
  `bai run --workspace ... "dev test ..."`.
- Uses isolated `BAI_HOME` and an external `tmp_path` workspace.
- Runs a trusted failing pytest command with `-B` and `no:cacheprovider` to
  avoid workspace cache/bytecode writes.
- Requires a public `optic_trace_artifact` under runtime workflow state.
- Proves `BugFixStructuralLoopOptic` role order, typed handoffs, lineage,
  per-role written artifact records, no mutation approval, no source change,
  and invalid composition fail-closed behavior.

Red Team Auditor rounds:
- First verdict: `REPAIR TEST BEFORE IMPLEMENTATION`.
  - Gaps: role write records were too weak, exact handoff matrix was missing,
    invalid composition fail-closed was absent, final artifact coverage was
    incomplete, and Patcher/Reviewer statuses overclaimed behavior.
- Second verdict: `REPAIR TEST BEFORE IMPLEMENTATION`.
  - Gaps: workspace isolation was not proven against pytest cache writes,
    role written records were not tied to concrete artifacts, no-mutation
    behavior was under-asserted, and Patcher/Reviewer statuses still implied
    unsupported patch generation/review.
- Third verdict: `ACCEPT`.
  - No blocking gaps remained.

Corrections made before implementation:
- Changed request to current supported CLI shape: `dev test <argv>`.
- Added exact workspace file-set assertion.
- Added exact role input/output/lineage expectations.
- Added exact typed handoff matrix including `input_type`.
- Added per-role written artifact records tied to actual trace/audit/fix
  artifacts.
- Added assertions for no proposed, applied, denied, or approved mutation.
- Clarified structural-only statuses:
  `not_applied_no_patchdiff` and `completed_structural_no_patchdiff`.

Implementation changes after Red Team acceptance:
- `src/bai/execution/effects.py`
  - Added `TestCommandRunError` so failed test command metadata remains
    available for failed workflow artifacts.
- `src/bai/artifacts/workflow.py`
  - Added declarative bug-fix optic trace construction from the validated
    `BugFixStructuralLoopOptic`.
  - Added workflow-state trace writing under
    `BAI_HOME/state/workflows/optic_traces/`.
  - Kept failed test node status as `failed` while retaining failed test-run
    evidence.
- `src/bai/execution/finalize.py`
  - Writes optic trace artifacts for successful dev workflows before final
    workflow binding.
- `src/bai/execution/harness.py`
  - Writes optic trace artifacts for expected failed dev workflows and includes
    the trace path in the failed workflow artifact.

Validation:
- `pytest tests/e2e/test_optic_category_bugfix_workflow.py -q` -> `2 passed`
- `pytest tests/e2e/test_optic_category_bugfix_workflow.py tests/e2e/test_bug_fix_structural_loop_optic.py tests/dev/test_optic_category.py tests/e2e/test_cli_dev_workflow.py tests/dev/test_developer_workflow_contract.py tests/dev/test_test_command.py tests/dev/test_audit.py tests/dev/test_fix.py -q` -> `62 passed`
- `pytest -q` -> `212 passed`

## Bug-Fix Workflow E2E Stress Completion

Test design summary:
- Expanded `tests/e2e/test_optic_category_bugfix_workflow.py` to the full six
  e2e stress tests:
  - structural bug-fix trace through the real CLI with isolated `BAI_HOME` and
    external workspace
  - missing handoff fails closed by direct composition validation
  - role write-surface violation fails closed without file mutation
  - approved explicit `propose-modify` patch applies only to `calc.py`
  - unapproved explicit patch remains proposal-only/denied
  - invalid composition and request text cannot broaden visibility
- Kept malformed graph cases as direct validator construction because the CLI
  has no malformed composition surface.
- Used explicit mutation proposals rather than autonomous patch generation.
- Asserted runtime artifacts stay under `BAI_HOME`, trusted test commands record
  `trusted_command_no_sandbox`, mutation approvals bind preimage/content/task,
  and optic traces record role order, handoffs, lineage, read projections, and
  mutation evidence.

Red Team pre-implementation verdict:
- First verdict: `REPAIR BEFORE ACCEPTANCE`.
  - Gaps: secret-view tests could be hard-coded around prompt examples,
    mutation approval evidence was too weak, and composition validation did not
    prove role write surfaces remained unchanged.
- Second verdict: `REPAIR BEFORE ACCEPTANCE`.
  - Gap: Reviewer `docs/agents/` authority needed negative assertions without
    `AgentMapDelta` and with `ReviewReport`.
- Final pre-implementation verdict: `ACCEPT`.
  - Approved the explicit `propose-modify` substitute for unsupported
    autonomous patching and exact trace assertions as declared artifact
    contract checks.

Failures found after accepted tests:
- `pytest tests/e2e/test_optic_category_bugfix_workflow.py -q` initially
  returned `3 failed, 3 passed`.
- Real gaps:
  - CLI parser had no explicit `propose-modify <path> --content <text>` request
    shape, so approved/unapproved patch scenarios could not produce a modify
    proposal.
  - `CompositionSpec.input_types` accepted unused undeclared views such as
    `OpaqueTelemetryEnvelope`.
  - Bug-fix optic trace role steps did not bind applied mutation evidence to
    mutation approval artifacts.

Implementation repairs:
- `src/bai/execution/agent.py`
  - Added a narrow `propose-modify` parser that emits an explicit phase-one
    `modify` proposal with fixed `--content` syntax.
- `src/bai/optic_category.py`
  - Composition validation now rejects input types not consumed by any source
    optic as undeclared visibility.
- `src/bai/artifacts/workflow.py`
  - Bug-fix role steps now include mutation evidence for `PatcherOptic` and
    `ReviewerOptic`, binding path, operation, and mutation approval artifact.
- `src/bai/execution/finalize.py` and `src/bai/execution/harness.py`
  - Passed mutation approval paths into optic trace construction on success and
    expected failure paths.

Red Team post-repair verdict:
- `ACCEPT`.
- No critical issues found.
- Approved deviations:
  - direct validator construction for malformed compositions
  - explicit `propose-modify` instead of autonomous bug fixing
  - `trusted_command_no_sandbox` semantics
- Debt note: trace builder size is larger than ideal but remains deterministic
  artifact construction, not a workflow engine or new authority layer.

Validation:
- `pytest tests/e2e/test_optic_category_bugfix_workflow.py -q` -> `6 passed`
- `pytest tests/dev/test_optic_category.py tests/e2e/test_bug_fix_structural_loop_optic.py tests/e2e/test_cli_dev_workflow.py tests/dev/test_developer_workflow_contract.py tests/dev/test_approvals.py tests/dev/test_workflow_context.py -q` -> `52 passed`
- `pytest -q` -> `216 passed`
