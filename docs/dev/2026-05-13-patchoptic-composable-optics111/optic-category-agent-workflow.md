# Optic Category Agent Workflow: Audit -> Plan -> Patch -> Review

This note maps the `Code_Audit-Plan-Patch-Review` skill pattern into the
PatchOptic / bAI harness model.

The goal is not to make agents autonomous. The goal is to make multi-agent
collaboration mechanically traceable, phase-scoped, and enforceable through
declared visibility and write scopes.

## Mental Model

The skill set already behaves like an optic workflow:

```text
Roadmap Extractor -> Auditor -> Planner -> Patcher -> Reviewer
```

Each role sees a different projection of the same run state and can write only
specific artifacts.

The Optic Category interpretation is:

```text
object = typed document/state view
morphism = role optic that transforms one allowed view into another artifact
composition = validated handoff between role optics
```

The key practical point:

```text
role output becomes a typed artifact;
the next role can consume it only if its optic declares that input type.
```

## Objects: Document / State Types

These are the typed states that role optics pass around.

```text
RepositoryMapView
IssueClaimView
AuditEvidenceView
AuditReport
RepairPlan
PatchDiff
ReviewReport
AgentMapDelta
RunDecision
```

They can be represented as Markdown files with structured sections at first.
They do not need a full schema language in the first implementation.

## Primitive Role Optics

### RoadmapExtractorOptic

```text
inputs:
  - repository files
  - docs/agents/

output:
  - RepositoryMapView

read projection:
  - architecture layers
  - file inventory
  - contracts/protocol surfaces
  - lifecycle flow
  - declared invariants
  - test coverage map
  - ownership/boundary map

write surface:
  - docs/agents/ only, if generating or refreshing maps

lineage:
  - file paths and doc paths used to construct the map
```

This optic is reference-only. It extracts structure but does not classify,
plan, patch, or review.

### AuditorOptic

```text
inputs:
  - RepositoryMapView
  - IssueClaimView

output:
  - AuditReport

read projection:
  - docs/agents/
  - relevant source paths
  - relevant tests
  - deterministic reproduction scripts/logs under docs/audits/<domain>/

write surface:
  - docs/audits/<domain>/scripts/
  - docs/audits/<domain>/logs/
  - docs/audits/<domain>/reports/

forbidden:
  - production code writes
  - docs/agents/ writes
  - repair plans
  - patches

lineage:
  - execution path map
  - exact raiser
  - violated precondition
  - reproduction or falsification evidence
```

Auditor transforms a claim into evidence. It does not transform code.

### PlannerOptic

```text
inputs:
  - RepositoryMapView
  - AuditReport

output:
  - RepairPlan

read projection:
  - docs/agents/
  - docs/audits/<domain>/reports/
  - relevant source paths

write surface:
  - docs/audits/<domain>/plan/plan.md

forbidden:
  - production code writes
  - patch execution
  - review approval

lineage:
  - audit report references
  - invariant violation point
  - repair location
  - validation/falsification plan
```

Planner transforms proven evidence into a minimal repair directive.

### PatcherOptic

```text
inputs:
  - RepairPlan
  - AuditReport

output:
  - PatchDiff

read projection:
  - docs/audits/<domain>/reports/
  - docs/audits/<domain>/plan/plan.md
  - exact source/test files named by the plan

write surface:
  - exact source/test files authorized by the plan

forbidden:
  - redesign
  - new abstractions unless explicitly justified by plan
  - docs/agents/ writes
  - audit/planning/review artifacts

lineage:
  - plan section authorizing the changed file/line
  - test(s) updated or run
  - minimal diff summary
```

Patcher transforms a repair plan into a diff. It does not decide the repair.

### ReviewerOptic

```text
inputs:
  - RepositoryMapView
  - AuditReport
  - RepairPlan
  - PatchDiff

output:
  - ReviewReport
  - optional AgentMapDelta

read projection:
  - docs/agents/
  - docs/audits/<domain>/reports/
  - docs/audits/<domain>/plan/
  - patch diff
  - relevant tests

write surface:
  - docs/audits/<domain>/reports/review.md
  - docs/audits/<domain>/review/
  - docs/agents/ only for map/contract corrections

forbidden:
  - implementing the patch
  - adding new design
  - masking failures

lineage:
  - quoted invariant from plan
  - patch diff references
  - validation commands and results
```

Reviewer transforms a patch into an approval or rejection artifact.

## Concrete Composition

The validated pipeline is:

```text
RoadmapExtractorOptic
  -> AuditorOptic
  -> PlannerOptic
  -> PatcherOptic
  -> ReviewerOptic
```

In type form:

```text
RepositoryFiles -> RepositoryMapView

(RepositoryMapView, IssueClaimView)
  -> AuditReport

(RepositoryMapView, AuditReport)
  -> RepairPlan

(AuditReport, RepairPlan)
  -> PatchDiff

(RepositoryMapView, AuditReport, RepairPlan, PatchDiff)
  -> ReviewReport
```

The categorical upgrade is that this composition can be validated once and
then reused as a named composed optic:

```text
BugFixStructuralLoopOptic:
  inputs:
    - RepositoryFiles
    - IssueClaimView

  outputs:
    - ReviewReport
    - optional PatchDiff
    - optional AgentMapDelta

  source_optics:
    - RoadmapExtractorOptic
    - AuditorOptic
    - PlannerOptic
    - PatcherOptic
    - ReviewerOptic
```

The composed optic remains safe only if each handoff preserves the declared
input/output type and lineage contract.

## Public And Role-Local Documents

### Public Run Document

Location:

```text
docs/dev/<run>/communication-log.md
```

Purpose:

```text
shared coordination surface
role handoff summaries
decisions
evidence pointers
validation results
open questions
```

Every role can read this file. The orchestrator owns final decisions in it.

### Role-Local Artifacts

For operational audit/fix loops, role-local artifacts live under:

```text
docs/audits/<domain>/
  scripts/
  logs/
  reports/
  plan/
  review/
```

Role visibility is intentionally different:

```text
Auditor:
  writes reports/scripts/logs
  cannot write plan or source

Planner:
  reads reports
  writes plan
  cannot patch

Patcher:
  reads reports + plan
  writes authorized source/test diff
  cannot update docs/agents/

Reviewer:
  reads all prior artifacts + diff
  writes review
  may update docs/agents/ only for proven map/contract drift
```

## Debate And Collaboration Mechanics

Agents do not debate through private side channels. They exchange typed
artifacts and short public claims.

Recommended public-message form:

```text
claim:
evidence:
artifact written:
requested next role:
blocking question:
```

Example:

```text
Auditor claim:
  The failure is an ownership-bound internal invariant violation.

Evidence:
  docs/audits/mutation/reports/preimage-race.md
  docs/audits/mutation/logs/repro.json

Requested next role:
  Planner should locate the repair boundary.
```

The orchestrator validates whether the next handoff is legal:

```text
AuditReport can feed PlannerOptic
RepairPlan can feed PatcherOptic
PatchDiff can feed ReviewerOptic
```

## Why This Is Optic Category Instead Of Just Chat

Plain multi-agent chat:

```text
agents talk
someone decides what seems right
patch happens
```

Optic Category workflow:

```text
each role has a declared projection
each role writes a typed artifact
handoffs are validated by artifact type and lineage
write authority is scoped by role
the composed workflow can be reused as BugFixStructuralLoopOptic
```

The category-like property appears when the whole loop can be treated as one
larger optic with the same shape as the smaller optics:

```text
input view -> output artifact
```

That makes primitive roles and composed workflows interchangeable for later
planning, auditing, and reuse.

## Minimal Harness Realization

The first implementation should not build a full graph engine.

Start with:

```text
OpticSpec
  id
  inputs
  outputs
  read_projection
  write_surface
  lineage_required

CompositionSpec
  id
  source_optics
  handoffs
  resulting_inputs
  resulting_outputs

CompositionValidator
  validate input/output compatibility
  validate lineage preservation
  validate write surfaces do not broaden
```

Then store only:

```text
primitive role optics
named composed optics actually used
per-run validation record
```

Do not materialize every possible composition.

