 # Optic Category Agent Workflow

  This document maps the source workflow skills into the Optic Category model.

  Raw source material:
  - source-workflow-skills.md

  ## Core Mapping

  Roles are optics.

  Each role optic declares:
  - input artifact/view types
  - output artifact types
  - read projection
  - write surface
  - lineage requirements

  The full bug-fix loop is a composed optic.

  ## Primitive Role Optics

  ### RoadmapExtractorOptic
  Inputs:
  - RepositoryFiles

  Outputs:
  - RepositoryMapView

  Write surface:
  - docs/agents/

  ### AuditorOptic
  Inputs:
  - RepositoryMapView
  - IssueClaimView

  Outputs:
  - AuditReport

  Write surface:
  - docs/audits/<domain>/scripts/
  - docs/audits/<domain>/logs/
  - docs/audits/<domain>/reports/

  Forbidden writes:
  - production source
  - docs/agents/
  - docs/audits/<domain>/plan/

  ### PlannerOptic
  Inputs:
  - RepositoryMapView
  - AuditReport

  Outputs:
  - RepairPlan

  Write surface:
  - docs/audits/<domain>/plan/

  Forbidden writes:
  - production source

  ### PatcherOptic
  Inputs:
  - AuditReport
  - RepairPlan

  Outputs:
  - PatchDiff

  Write surface:
  - exact source/test files authorized by RepairPlan

  Forbidden writes:
  - docs/agents/
  - audit reports
  - plan files

  ### ReviewerOptic
  Inputs:
  - RepositoryMapView
  - AuditReport
  - RepairPlan
  - PatchDiff

  Outputs:
  - ReviewReport
  - optional AgentMapDelta

  Write surface:
  - docs/audits/<domain>/reports/review.md
  - docs/audits/<domain>/review/
  - docs/agents/ only for AgentMapDelta

  ## Composed Optic

  BugFixStructuralLoopOptic

  Inputs:
  - RepositoryFiles
  - IssueClaimView

  Outputs:
  - ReviewReport
  - optional PatchDiff
  - optional AgentMapDelta

  Source optics:
  - RoadmapExtractorOptic
  - AuditorOptic
  - PlannerOptic
  - PatcherOptic
  - ReviewerOptic

  ## Validation Rules

  The implementation should validate:
  - every consumed input type is produced upstream or externally provided
  - handoff output type matches target input type
  - role write surfaces are not broadened by composition
  - lineage requirements are satisfied
  - optional outputs are explicitly marked
  - invalid composition fails closed

  ## Non-Goals

  This is not:
  - autonomous agent execution
  - agent-to-agent chat
  - workflow engine
  - patch automation
  - benchmark/eval
  - dynamic LLM-selected visibility

