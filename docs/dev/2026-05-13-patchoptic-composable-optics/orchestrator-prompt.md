  You are upgrading the old non-Categorical design to the real-Categorical design, and after that you are implementing the first Optic Category structure in bai-harness.

  This is a test-driven implementation task.

  Context:
  We want the harness to support the Optic Category abstraction for structured
  multi-agent workflows.

  The concrete first case is the bug-fix structural loop inspired by the
  existing role skills:

    RoadmapExtractor -> Auditor -> Planner -> Patcher -> Reviewer

  Each role is an optic:
    input view(s)
    read projection
    write surface
    required lineage
    output artifact type

  The composed workflow is also an optic:

    BugFixStructuralLoopOptic:
      inputs:
        RepositoryFiles
        IssueClaimView
      outputs:
        ReviewReport
        optional PatchDiff
        optional AgentMapDelta

  Why:
  We want multi-agent collaboration to be traceable and enforceable, not just
  chat. Agents may discuss, but the real handoff is through typed artifacts and
  validated composition. The system should know which role can read what, write
  what, and consume which prior artifact.

  Read first:
  - docs/PLAN.md
  - docs/RESOURCES.md
  - README.md
  - docs/dev/README.md
  - docs/dev/2026-05-13-patchoptic-composable-optics/orchestrator-prompt.md
  - docs/dev/2026-05-13-patchoptic-composable-optics/communication-log.md
  - docs/dev/2026-05-13-patchoptic-composable-optics/optic-category-agent-
  workflow.md
  - src/bai/*
  - tests/dev/*
  - tests/e2e/*

  Goal:
  Implement the smallest Optic Category structure needed to represent and
  validate the bug-fix structural loop.

  Do not implement autonomous agents.
  Do not implement agent-to-agent chat.
  Do not implement a workflow engine.
  Do not implement benchmark/eval.
  Do not implement a full graph runtime.
  Do not implement actual patching through this new layer yet.

  This slice is structural:
  - define optic specs
  - define composition specs
  - validate typed handoffs
  - validate read/write surfaces
  - validate lineage requirements
  - persist or expose the named composed optic
  - add e2e tests proving the bug-fix workflow composition is valid and
  constrained

  Recommended module direction:
  Prefer a small module such as:

    src/bai/optic_category.py

  or, if the package organization suggests it:

    src/bai/optics/category.py

  Keep it small. Do not split into many modules unless ownership is genuinely
  separate.

  Expected core types:
  - OpticSpec
  - CompositionSpec
  - OpticHandoff
  - CompositionValidationResult or equivalent
  - OpticCategoryRegistry or equivalent

  Each OpticSpec should minimally capture:
  - optic_id
  - input_types
  - output_types
  - read_projection
  - write_surface
  - lineage_required
  - role or phase metadata if useful

  Each handoff should capture:
  - from_optic
  - output_type
  - to_optic
  - input_type

  Each composition should capture:
  - composition_id
  - source_optics
  - handoffs
  - resulting_input_types
  - resulting_output_types
  - validation record / lineage summary

  Test-first requirements:

  1. Unit/dev tests for primitive role optics
     - RoadmapExtractorOptic has output RepositoryMapView.
     - AuditorOptic consumes RepositoryMapView + IssueClaimView and outputs
  AuditReport.
     - PlannerOptic consumes RepositoryMapView + AuditReport and outputs
  RepairPlan.
     - PatcherOptic consumes AuditReport + RepairPlan and outputs PatchDiff.
     - ReviewerOptic consumes RepositoryMapView + AuditReport + RepairPlan +
  PatchDiff and outputs ReviewReport.
     - Each role has explicit read projection and write surface.
     - Auditor cannot write production code.
     - Patcher cannot write docs/agents.
     - Reviewer may write docs/agents only for AgentMapDelta / contract drift.

  2. Unit/dev tests for composition validation
     - Valid BugFixStructuralLoopOptic composition passes.
     - Missing AuditReport -> Planner handoff fails.
     - Patcher receiving only AuditReport without RepairPlan fails.
     - Reviewer without PatchDiff fails unless composition explicitly marks
  patch as absent/noop.
     - A handoff with mismatched output/input type fails.
     - A role attempting to consume a type it did not declare fails.
     - A role attempting to write outside its write surface fails.
     - Lineage requirements must be satisfied by upstream artifacts.

  3. E2E tests for the concrete bug-fix workflow
     - Build/register primitive role optics.
     - Compose them into BugFixStructuralLoopOptic.
     - Validate the composition.
     - Assert the composed optic has:
       - inputs: RepositoryFiles, IssueClaimView
       - outputs: ReviewReport, optional PatchDiff, optional AgentMapDelta
       - source optics in correct order
       - typed handoffs recorded
       - lineage summary preserved
     - Assert invalid composition fails closed.
     - Assert no runtime files are written outside BAI_HOME if persistence is
  added.
     - Assert this layer does not invoke Harness effects, test commands,
  mutation writes, scheduler, or agents.

  4. Documentation/traceability tests if practical
     - docs/dev/<run>/optic-category-agent-workflow.md exists.
     - The bug-fix role names in code/tests match the design doc.

  Implementation requirements:
  - Keep everything deterministic.
  - Keep validation mechanical.
  - No LLM calls.
  - No subprocess calls.
  - No filesystem crawling.
  - No dynamic agent spawning.
  - No parallelism.
  - No broad shell runner.
  - No schema overbuild.
  - No category-theory-heavy API names unless they clarify implementation.
  - Use plain Python standard library.
  - Preserve all existing tests.
  - Add comments/docstrings explaining:
    - graph vs category distinction
    - primitive optic vs composed optic
    - why composition is validated and saved
    - why this does not execute the workflow

  Workflow:
  1. Add failing tests first.
  2. Run focused tests to confirm failures.
  3. Implement minimal structure.
  4. Run focused tests.
  5. Run full pytest -q.
  6. Update docs only if needed for user-visible or architecture-visible
  behavior.
  7. Report:
     - tests added
     - files changed
     - behavior added
     - what remains intentionally out of scope
     - validation results

  Agent coordination:
  Spawn or assign four roles:

  1. Planner subagent
     Responsibility:
     - refine the minimal Optic Category implementation plan
     - decide exact module boundary and test file placement
     - ensure no overbuild

  2. Implementation agent
     Responsibility:
     - write tests first
     - implement the minimal code
     - keep diffs small and deterministic

  3. Auditor
     Responsibility:
     - verify semantic alignment:
       - declared views, not LLM-requested fields
       - role optics have bounded read/write surfaces
       - composition preserves typed handoffs and lineage
       - composed optic does not broaden authority

  4. Red Team
     Responsibility:
     - check technical debt:
       - abstraction inflation
       - stale abstractions
       - symptom patches
       - unsafe-by-convention APIs
       - missing comments/docstrings
       - file size growth
       - tests that assert implementation details instead of invariants

  The orchestrator should first ask each role for a short plan, then synthesize
  one implementation path. The final merge should happen only after Auditor and
  Red Team reports are addressed or explicitly deferred.

  Short role prompts:

  Planner subagent:
  Design the smallest implementation plan for Optic Category support around the
  bug-fix structural loop. Decide module boundary, core types, tests, and scope
  exclusions. Optimize for minimal code and mechanical validation.

  Implementation agent:
  Add failing tests first for primitive role optics, composition validation, and
  the BugFixStructuralLoopOptic e2e case. Then implement the smallest
  deterministic code to pass them. Do not add execution, agents, subprocesses,
  or workflow runtime behavior.

  Auditor:
  Check whether the design preserves PatchOptic semantics: declared views,
  bounded write surfaces, typed handoffs, lineage, and fail-closed validation.
  Reject any design where the LLM can broaden visibility or where composition
  broadens authority.

  Red Team:
  Review for technical debt and boundary risks: abstraction inflation, graph
  engine creep, unsafe-by-convention APIs, stale functions, missing comments/
  docstrings, weak tests, file-size growth, and semantic drift from Optic
  Category into chat orchestration.