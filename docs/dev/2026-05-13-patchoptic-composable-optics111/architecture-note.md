# PatchOptic Composable Optics Architecture Note

Date: 2026-05-13

## Scope

This slice adds the smallest internal structure needed to move from repeated
workflow field rules toward reusable composable optics. It does not rewrite
workflow schemas, add a PatchOptic dependency, introduce a workflow engine, or
change Harness orchestration.

## Module Shape

The new `bai.core.optics` module is a pure contract layer:

- `ViewSpec`: typed field set accepted or produced by an optic.
- `PatchRule`: declared write surface for a projected field.
- `Optic`: typed projection plus allowed patch surface and lineage contract.
- `OpticGraph`: mechanical composition of compatible optics.
- `WorkflowOpticRef`: workflow/phase/role/resource key.
- `OpticRegistry`: maps workflow keys to reusable optic names.

The module is intentionally inert. It does not read workspace files, run
commands, call models, write artifacts, or make policy decisions from LLM
claims. Callers provide source data and explicit lineage; the optic only
projects declared fields and validates patches against visible evidence.

## Enforcement Invariants

- Visibility is declared by `Optic.output_view`.
- A requested field outside the declared projection raises
  `OpticPermissionError`.
- Optics compose only when the producer output type satisfies the consumer
  input type and required fields.
- Patchable fields must be listed in `patch_rules`.
- Patch operations must be listed on the matching `PatchRule`.
- Patch evidence must be drawn from lineage tokens visible in the projected
  view.
- Required lineage fields must be visible and cited by the patch evidence.
- Workflow policy references optic names through `OpticRegistry`; raw field
  rules stay in optic declarations.

## Integration Boundary

`PolicyEngine` remains the runtime gate. The optics module does not replace
effect gating, artifact path checks, mutation approval binding, preimage
validation, or atomic writes. A future integration can compile resolved optic
graphs into context/workflow artifact metadata, then feed the existing policy
gate before any read, command, or write.

## Deliberate Non-Goals

- No schema language or graph DSL.
- No LLM-driven field selection.
- No automatic workspace discovery or crawling.
- No delete mutation support.
- No test-command sandbox semantics beyond the current trusted-command model.
- No runtime dependency on external PatchOptic code.

## Validation

The focused tests in `tests/dev/test_optics.py` prove that:

- an optic declares input view, output projection, allowed patch surface, and
  lineage requirements
- compatible optics compose and incompatible typed interfaces fail
- workflow policy can reference optic ids instead of duplicating fields
- requested extra visibility is rejected
- writes require visible lineage evidence
- undeclared patch fields and operations are rejected
- malformed optic declarations fail closed

`tests/dev/test_policy_gates.py` now also rejects `delete` mutation proposals at
agent-output policy time, aligning plan validation with the existing mutation
execution boundary.
