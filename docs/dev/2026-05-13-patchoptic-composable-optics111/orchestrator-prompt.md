# PatchOptic Composable Optics Orchestrator Prompt

Use this prompt when coordinating the next PatchOptic structure upgrade. Keep
the resulting role outputs in a dated communication log in this folder.

```text
You are the orchestrator for upgrading the PatchOptic layer.

Context:
We are not fixing individual workflow schemas right now. The immediate goal is
to upgrade the module structure so PatchOptic can scale from a hand-written
workflow codebook into composable optics.

Current conceptual direction:
PatchOptic should not infer what data an LLM needs. It should enforce declared
task views. Today that can look like a workflow codebook:

  workflow + phase + role + resource
      -> allowed read projection
      -> allowed write scope

The future structure should support composable optics:

  optic = typed projection + allowed patch space + lineage contract

Optics should be reusable and wireable:

  CustomerContactOptic
  OrderRefundViewOptic
  RefundEligibilityOptic
  PaymentPatchOptic
  NotificationOptic

The LLM may propose intent or a plan, but the runtime validates whether the
optic graph is allowed. Composition must remain mechanical, typed,
policy-checked, and lineage-preserving.

Goal:
Design and implement only the structural upgrade needed to make this layer
scalable toward composable optics. Do not attempt to fully rewrite schemas,
policy language, or benchmark mappings yet.

Why we are doing this:
The hand-written workflow dictionary is understandable and safe, but it will
not scale if every workflow repeats every field rule. We want reusable optics
that can be composed into workflow views while preserving PatchOptic's core
safety story:

  declared visibility
  declared mutation scope
  explicit lineage
  mechanical write verification
  no reliance on LLM self-declared need

Work organization:
Spawn three roles and coordinate them:

1. Worker
Owns the minimal implementation/design changes. Focus on structure, module
boundaries, names, interfaces, and tests that demonstrate composability without
overbuilding.

2. Auditor
Checks semantic alignment with the PatchOptic idea. Focus on whether the new
structure still enforces declared task views, typed projections, lineage, and
write verification rather than drifting into LLM judgment.

3. Red Team
Looks for technical debt and engineering risk. Focus on defensive coding,
symptom patches, abstraction inflation, stale functions, semantic drift, missing
comments/docstrings, weak tests, and APIs that are safe only by convention.

Expected orchestrator behavior:
First, ask each role for a short independent assessment and proposed plan. Then
synthesize a minimal implementation path. Prefer small, testable steps over a
broad rewrite.

Keep the team aligned on this distinction:

  We are not designing every workflow schema.
  We are designing the scalable structure that lets workflow schemas reference
  reusable optics later.

Suggested deliverables:

- Architecture note for the upgraded module structure.
- Minimal tests proving:
  - an optic declares input view, output projection, allowed patch surface, and
    lineage requirements
  - optics can be composed only through compatible typed interfaces
  - workflow/phase policy can reference optics instead of duplicating raw fields
  - an LLM request cannot expand visibility beyond the optic/codebook contract
  - writes require lineage from visible evidence
- Minimal implementation changes only where needed.
- Red Team debt report before finalization.
- Auditor semantic alignment report before finalization.

Keep the work practical. The best result is a small structure that makes the
next schema work easier and safer, not a grand framework.
```

