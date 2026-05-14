# Scaffold/Audit/Manual-Control E2E

## Test Design

Added `tests/e2e/test_scaffold_audit_manual_control.py` as an end-to-end
suite for the practical developer workflow:

- bAI scaffolds dev tasks and writes workflow/context/audit/memory artifacts
- unapproved code proposals remain proposal-only and audited
- manual approval applies exactly one scoped path
- approval cannot broaden scope to unrelated or outside paths
- trusted test commands are audit evidence, not a write sandbox
- failing tests create failure audit/fix evidence without success memory
- background dev tasks remain scheduler metadata only
- `BugFixStructuralLoopOptic` remains structural trace metadata, not executed
  role agents

The final design runs the declared `bai.cli:main` entrypoint in subprocesses
with explicit `BAI_HOME`, external workspaces, recursive workspace snapshots,
and runtime/repo/workspace artifact path checks.

## Red Team Pre-Implementation

Initial verdict: `REPAIR TEST DESIGN`.

Critical findings:

- the first draft used in-process `run_cli` instead of declared CLI subprocess
  coverage
- approved mutation scope did not prove the only byte delta was the approved
  file
- background mode did not snapshot the full workspace
- optic trace checks allowed placeholder role artifacts
- no-role-agent evidence was too metadata-only and needed clearer wording
- failed-test no-success-memory checks only covered the workspace id memory
  path, not runtime-wide memory

Accepted repaired-design verdict: `ACCEPT TEST DESIGN`.

Accepted deviations:

- tests prove externally observable behavior and structural metadata, not the
  impossibility of hidden internals.
- no-source-mutation proof is scoped to the test workspace files, not the
  entire repository filesystem.
- the Optic Category case uses explicit `propose-modify` denial as
  manual-control evidence, not autonomous bug fixing.

## Repairs

Accepted tests exposed one real contract gap:

- `written_artifacts` in bug-fix optic role steps was accepting the optic trace
  file itself as a placeholder artifact for structural/no-output roles.

Implementation repair:

- `src/bai/artifacts/workflow.py`
  - `_bug_fix_role_written_artifacts` now returns real role-produced artifacts
    only: `audit_artifact` for `AuditorOptic`, `fix_artifact` for
    `PlannerOptic`, and `[]` for no-output, denied, or structural-only roles.

Test repair for existing expectations:

- `tests/e2e/test_optic_category_bugfix_workflow.py`
  - updated older optic e2e assertions to expect empty `written_artifacts` for
    no-output roles instead of `optic_trace_role` placeholders.

No `try/except` was added.

Repair rationale:

- invariant violated: `written_artifacts` should list role-produced output
  artifacts, not the trace file that records the role step.
- correct enforcement layer: `_bug_fix_role_written_artifacts`, where role-step
  artifact evidence is serialized.
- no redesign needed: existing `status` and `mutation_evidence` fields already
  carry no-op, denial, and manual-control evidence.
- the repair does not mask the issue because tests now assert empty artifacts
  for structural/no-output roles.

## Red Team Post-Repair

Verdict: `ACCEPT`.

Critical issues: none.

Medium/low debt:

- `_bug_fix_role_written_artifacts` still accepts `optic_trace_path` but no
  longer uses it.
- mutation evidence currently truncates if applied changes outnumber mutation
  approvals; current execution pairs them, but a future hardening pass should
  fail closed on mismatch.
- `tests/e2e/test_scaffold_audit_manual_control.py` is a large e2e sentinel.
- historical docs under the earlier optic run mention written artifacts tied
  to trace/audit/fix; behavior is now audit/fix only.

Approved deviations:

- `written_artifacts` now records only real role-produced audit/fix artifacts.
- `trusted_command_no_sandbox` remains explicit direct-execution evidence, not
  confinement.
- background mode remains metadata-only and does not invoke Harness.

## Validation

Focused:

```text
pytest tests/e2e/test_scaffold_audit_manual_control.py tests/e2e/test_optic_category_bugfix_workflow.py -q
14 passed in 2.46s
```

Required focused command:

```text
pytest tests/e2e/test_scaffold_audit_manual_control.py -q
8 passed in 1.73s
```

Full:

```text
pytest -q
232 passed in 6.92s
```

## Reviewer

Verdict: `APPROVE`.

Reason:

- tests enforce the practical workflow boundary: bAI scaffolds, traces,
  audits, and records proposal/fix evidence while source mutation requires
  explicit scoped approval
- Red Team findings were repaired or explicitly accepted as debt
- implementation repair was minimal and localized
- focused and full validation passed

Remaining non-scope work:

- remove unused `optic_trace_path` parameter from
  `_bug_fix_role_written_artifacts`
- harden mutation-evidence approval count mismatch
- consider splitting the large sentinel e2e if it grows further
- update older historical docs that mention the prior trace placeholder
  artifact behavior
