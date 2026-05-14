# Phase 1 Acceptance E2E

## Test Design

Added `tests/e2e/test_phase_one_acceptance.py` as an end-to-end Phase 1
acceptance suite with eight CLI-driven scenarios:

- global CLI requires explicit workspace
- workspace lifecycle and `BAI_HOME` separation
- plan-only foreground lifecycle artifacts
- inspect read-only path scope
- mutation approval and workspace scope
- trusted test command policy
- developer workflow artifact DAG without autonomous agents
- background metadata-only deferral with foreground priority

The final design runs the declared `bai.cli:main` entrypoint in subprocesses
with explicit `BAI_HOME` and cwd, then validates documented runtime JSON
artifacts.

## Red Team Pre-Implementation

Initial verdict: `REPAIR TEST DESIGN`.

Critical findings:

- the first draft used in-process `run_cli` instead of subprocess CLI
  bootstrap
- `BAI_HOME` isolation did not prove two-home separation
- background coverage used an unapproved mutation, so Harness execution would
  not have produced a visible effect
- failed-run checks did not fully validate scheduler/workflow/memory state
- dev workflow checks were too weak to prove audit/fix causality
- read-only and proposal-only checks snapshotted names but not file contents

Accepted repaired-design verdict: `ACCEPT TEST DESIGN`.

Accepted deviations:

- CLI bootstrap is the declared entrypoint in a subprocess, not an installed
  console-script packaging test.
- The orchestrator-specified dev workflow DAG is:
  `plan -> code`, `code -> doc`, `code -> test`, `doc/test -> audit`,
  `audit -> fix`.

## Repairs

Implementation repairs: none.

Reason: after repairing the test design, the accepted acceptance suite passed
against the current implementation. The Patcher role was intentionally skipped
because no accepted test exposed a real Phase 1 implementation contract
violation.

Test-design repairs made:

- switched acceptance helpers to subprocess execution of the declared CLI target
- added two-home workspace isolation
- added cross-artifact task/workspace identity checks
- added scheduler/workflow/no-memory checks for failed foreground runs
- strengthened background test with an approved mutation that must not execute
- added workspace content snapshots for read-only/proposal-only flows
- added dev denied-mutation audit/fix assertions and non-autonomy checks

## Red Team Post-Repair

Verdict: `ACCEPT`.

Critical issues: none.

Medium/low debt:

- `tests/e2e/test_phase_one_acceptance.py` is a large acceptance sentinel.
  Future growth should be split or helperized.
- The acceptance file deeply covers create mutation approval, while
  `propose-modify` remains covered by other tests.
- `optic_trace_artifact` paths are included in generic artifact checks, but
  optic-trace semantics remain owned by existing optic workflow tests.

Approved deviations:

- Patcher skipped because accepted e2e tests passed.
- Pre-existing dirty implementation files were treated as prior Phase 1 work.
- The local plan stub is accepted as a Phase 1 contract.
- `trusted_command_no_sandbox` is explicitly asserted as trusted direct
  execution, not filesystem confinement.

## Validation

Focused:

```text
pytest tests/e2e/test_phase_one_acceptance.py -q
8 passed in 2.16s
```

Full:

```text
pytest -q
224 passed in 5.52s
```

## Reviewer

Verdict: `APPROVE`.

Reason:

- acceptance tests match the Phase 1 runtime/CLI contract and orchestrator DAG
- Red Team findings were repaired or explicitly accepted
- no implementation repairs were needed
- focused and full validation passed
- this log records the required design, verdicts, repairs, and validation

Remaining non-Phase-1 work:

- installed console-script packaging check
- future acceptance-suite cleanup/splitting if the sentinel grows
- scheduler worker, voice, explicit parallel, and long-term memory capabilities
  outside this slice
