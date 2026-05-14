# Development Coordination Notes

This folder keeps traceable records for multi-agent development work.

The goal is not to create bureaucracy. It is to make agent collaboration
auditable enough that a later reviewer can reconstruct:

- what task was assigned
- which agent role made which claim
- what evidence was inspected
- what decision was made
- what files were changed
- what tests were run
- what risks or debts remain

## Expected Use

For each non-trivial multi-agent slice, create a run folder:

```text
docs/dev/<run>/
```

Recommended run folder name:

```text
YYYY-MM-DD-short-topic/
```

Example:

```text
docs/dev/2026-05-13-patchoptic-composable-optics/
```

Each run folder should normally contain:

```text
orchestrator-prompt.md
communication-log.md
```

## Role Records

Each role should write a short entry rather than an unstructured transcript:

- `Worker`: implementation plan, changed files, tests run, open assumptions.
- `Auditor`: semantic alignment checks, accepted/rejected claims, missing tests.
- `Red Team`: debt risks, bypass paths, stale abstractions, defensive repairs.
- `Orchestrator`: final decision, integration notes, validation result.

## Traceability Rules

- Link every major claim to a file, test, doc section, or explicit assumption.
- Record disagreements and their resolution.
- Record rejected options when they matter for future maintenance.
- Keep prompts and outputs separate from final decisions.
- Do not treat an agent message as authority without evidence.
- Do not store secrets, API keys, private user data, or external credentials.

## Phase-One Boundary

For `bai-harness`, coordination records should preserve the same phase-one
constraints as the code:

- no autonomous agent loops
- no parallel code mutation
- no hidden project discovery
- no broad shell runner
- no benchmark/eval implementation unless explicitly scoped later
- no cloud/provider SDK dependency unless explicitly approved later
