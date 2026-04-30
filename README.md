# bAI-harness

bAI: Bai's Personal Agent Harness.

## Phase-One Usage

`bai` is a global CLI/runtime. User projects are not created inside this source
repository; they are registered explicitly as workspaces.

Runtime files live under `~/.bai` by default:

- `~/.bai/config/`: workspace configuration
- `~/.bai/state/`: approvals, task events, and workflow state
- `~/.bai/memory/`: session and working memory
- `~/.bai/logs/`, `~/.bai/cache/`, `~/.bai/sandbox/`: runtime support dirs

Set `BAI_HOME` to isolate runtime state:

```sh
export BAI_HOME=/tmp/bai-home
```

Register and inspect a workspace:

```sh
bai workspace add demo ~/projects/demo
bai workspace list
bai workspace show demo
bai workspace allow-test demo --argv '["python3", "-m", "pytest", "-q"]' \
  --writable-path ~/projects/demo/.pytest_cache
```

Show or override the local provider stub:

```sh
bai provider show
bai provider set-local --model local-plan-stub --endpoint stub://local/plan
```

Workspace ids are authoritative. Display-name lookup uses a small runtime name
index; if that index is missing or stale, use `workspace show <workspace_id>` or
re-add the workspace.

Run a bounded phase-one request through Harness:

```sh
bai run --workspace demo "plan only"
bai --workspace demo "plan only"
bai run --workspace demo "inspect README.md"
bai run --workspace demo "test python3 -m pytest -q"
bai run --workspace demo "dev plan only"
```

Phase one defaults to serial execution, routes to a local model stub, records a
plan approval artifact under runtime state, and writes minimal session memory
under runtime memory.

Provider config lives under `~/.bai/config/providers.json` when explicitly set.
If it is missing, Router uses the built-in local stub:
`provider=local`, `model=local-plan-stub`, `endpoint=stub://local/plan`,
`network_required=false`. The phase-one Router accepts only local-only provider
endpoints and does not inspect prompts, memory, or workspace files.

Successful run JSON includes the phase-one artifact boundary:

- `task_id`, `execution_policy`, `workspace_id`, `model`, and `plan`
- `workflow_artifact` under `~/.bai/state/workflows/`
- `context_artifact` under `~/.bai/state/context/`
- `approval_artifact` and `approval_artifacts`
- `event_artifacts`
- `memory_artifact`, `working_memory_artifact`, and `memory_artifacts`
- `inspections`, `applied_changes`, and `denied_changes`
- `workflow_mode`, `node_results`, `audit_findings`, and `fix_proposals` for
  developer workflow requests

The workflow artifact is a minimal serial DAG record owned by Harness. It only
contains phase-one nodes that actually ran or were planned: `plan`, optional
`inspect`, optional approved `code_mutation`, and `memory_write`. It is not a
workflow engine and does not enable parallel execution.

`dev <request>` records the phase-one serial developer workflow contract:
`plan -> code -> doc -> test -> audit -> fix`. This is an artifact-level
contract, not an autonomous loop. The `code` node may contain bounded mutation
proposals, but mutations still require explicit approval. The `doc` and `fix`
nodes are proposal-only in phase one. The `test` node may run only through the
existing trusted `test <argv...>` path when configured. The `audit` node is
read-only and records deterministic findings from the current run state. Fixes
are never auto-applied.

The context artifact records the scoped bundle used for the plan agent:
request text, workspace metadata, serial execution policy, local router
constraints, and any explicitly approved memory snippets. It does not crawl
workspace files, include file contents, or add retrieval.

Session memory records the request and plan id. Working memory records current
workflow decision metadata such as task id, plan id, workspace id, risks, and
requested approval scope. Phase one still has no long-term memory store.

The `approval_artifact` field points to the agent-output acceptance record for
compatibility. `approval_artifacts` contains all approval records for the run,
including mutation approvals created from `--approve-mutation <path>`.
Completion is represented by task events, not by approval records.
Mutation approvals bind the approved path, operation, content hash, and expected
preimage observed immediately before the write. Mutation paths are policy-gated
before preimage inspection, and preimage hashes are streamed in bounded chunks.
Approved mutations are guarded by a per-target runtime lock under
`~/.bai/state/mutation_locks/`. Mutation content is written through
same-directory temporary files; create uses exclusive link semantics and modify
uses atomic replacement. If a stale fallback lock blocks work, the error names
both the target path and the lock path to resolve manually. Runtime artifact
writes and mutation replacements fsync the containing directory where the
platform supports it.

Each Harness run gets a `task_id` and writes structured task events under
`~/.bai/state/events/`, including `started` and either `completed` or `failed`.

The toy scheduler remains component-only scaffolding. It can record a
cooperative `running` -> `pause_requested` -> `paused` transition for one
background placeholder task, but there is no daemon, queue persistence, or
background command runner.

`inspect <path>` is a narrow read-only file inspection path. Paths are resolved
against the configured workspace root and must remain inside the workspace's
allowed paths. Inspection previews are capped and do not read the full file
before truncating.

`test <argv...>` is a narrow phase-one test command path. It is denied by
default and must be enabled in the workspace config with `workspace allow-test`.
The allow-test command accepts one exact `--argv` JSON array or one
`--argv-prefix` JSON array, plus optional `--writable-path` entries. Those
paths are recorded as `declared_writable_paths`; they must resolve inside the
workspace root or under `~/.bai/cache/`.

Test commands run with `shell=False`, cwd fixed to the canonical workspace root,
a short timeout, and bounded stdout/stderr previews. Nonzero exit and timeout
fail the Harness run, record a failed task event, and do not write memory.
Phase one does not provide a filesystem write sandbox for test commands. An
approved test argv is treated as trusted direct workspace execution, and
`test_runs[*].write_enforcement` is `trusted_command_no_sandbox`. The timeout
kills the immediate child process; commands that spawn their own process trees
remain a known limitation.

## Phase-One Limits

- No benchmark or eval runner.
- No self-improvement loop.
- No voice, Whisper, realtime voice, audio capture, or TTS.
- No LangGraph, APScheduler, Chroma, FAISS, Celery, or cloud API dependency.
- No LiteLLM, cloud provider fallback, provider health checks, or network model
  routing.
- No PatchOptic runtime dependency.
- No explicit parallel execution, autonomous agents, recursive delegation, or
  agent-to-agent chat.
- No project crawling or auto-discovery.
- No general shell command runner. Test execution is limited to the explicitly
  allowed `test <argv...>` path and does not enable workspace network,
  destructive, or Harness mutation permissions. It is trusted direct execution,
  not a write-confinement mechanism.
- Mutations require Harness approval and PolicyEngine gating, and are limited
  to approved paths inside the configured workspace. Proposed mutations that
  are not approved are reported in `denied_changes`, and the run status is
  `completed_with_denials`. Phase-one `create` mutations fail if the target
  already exists, and `modify` mutations fail if the target is missing.
- CLI user-facing failures return exit code `2`; unexpected internal failures
  return exit code `1`.
