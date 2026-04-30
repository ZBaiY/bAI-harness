# bAI Minimal Plan

## Goal

Build a minimal personal AI harness for:

- voice-first terminal interaction over SSH and push-to-talk
- strictly serial developer workflow: Plan -> Code -> Doc -> Test -> Audit -> Fix
- low-priority background tasks that are interruptible

Default execution is serial. Parallel execution is allowed only by explicit command:

- `//s`: force serial
- `//p`: force parallel

Intent words such as summarize, compare, overall, and audit may suggest parallelism, but must not silently override serial mode. The Harness should ask for confirmation or require `//p` before fan-out.

## Architecture

Initial components:

- Harness
- Agents
- PolicyEngine
- Router
- Scheduler
- Memory

PolicyEngine is a PatchOptic-style enforcement layer. It is deterministic and sits on the execution path between agent output and actual execution.

Voice is a core input/output adapter owned at the Harness boundary, not a sixth orchestration component. It has a contract because voice-first interaction is a core use case, but it should not grow into a separate agent framework.

No browser automation. No autonomous multi-agent loops. No generic chatbot layer.

## Runtime And Workspace Model

bAI is a global personal CLI/runtime, not a repository that contains all user projects.

Expected layout:

- `~/repos/bai-harness`: bAI source code only
- `~/projects/SoionLab`: external user workspace
- `~/projects/PolySoion`: external user workspace
- `~/.bai/`: runtime config, state, logs, memory, cache, and sandbox files

The CLI command should be `bai`.

The architecture must never assume that projects live under the `bai-harness` repository. All project access goes through explicit workspace configuration.

### Workspace Config

Each workspace record declares:

- `workspace_id`
- `name`
- `root_path`
- `allowed_paths`
- `default_branch` optional; only for Git-backed workspaces
- `command_policy`
- `network_policy`
- `sandbox_path`
- `memory_scope`

Workspace config lives under `~/.bai/config/`, not inside each project by default. A project-local opt-in file may be supported later, but the user-level config remains authoritative.

Default `memory_scope` is per-workspace.

Conservative defaults for `bai workspace add <name> <root_path>`:

- `allowed_paths`: the canonical workspace root, read-only until a task-specific approval grants mutation.
- `command_policy`: allow `inspect` by default for canonical `allowed_paths`; deny all other classes until approval.
- `network_policy`: off by default.
- `sandbox_path`: `~/.bai/sandbox/<workspace_id>/`.
- `memory_scope`: `workspace:<workspace_id>`.
- `default_branch`: detected only if the root is a Git repo; otherwise unset.
- sandbox mode: `in_place_read` by default.

### Command Policy

`command_policy` is a workspace-level ceiling. Task scope may narrow it but may not broaden it without approval.

Minimal command classes:

- `inspect`: read-only commands such as listing files, reading files, or checking Git status. Commands that create caches are not inspect commands.
- `test`: diagnostics or test commands that may write only to approved cache/build/temp locations.
- `mutate`: commands that write source files or project state.
- `workspace_network`: workspace commands that access the network.
- `destructive`: delete, reset, clean, force push, or overwrite operations.

Default stance:

- deny unknown commands.
- allow `inspect` inside canonical `allowed_paths` after workspace validation.
- deny `workspace_network` unless workspace and task scope both allow it.
- deny `destructive` unless explicitly approved for destructive class plus exact target paths.
- require Harness approval before any `mutate` command.
- command approval is bound to canonical workspace, command class, allowed paths, sandbox mode, and expiry.
- first-slice approval is class-based, not exact command-pattern based.
- model-provider network is governed by Router provider allowance, not `workspace_network`.

Initial approval flow after workspace creation:

1. `bai workspace add` creates read-only defaults.
2. `inspect` is available by default within canonical `allowed_paths`.
3. First `test` request prompts for approved command class and explicit writable cache/build/temp paths.
4. First `mutate` request requires a checkpoint-bound approval with exact target paths.
5. `workspace_network` and `destructive` always require explicit approval.

### Workspace Validation

Workspace validation is mandatory before any file read, command execution, memory write, or mutation.

Rules:

- `root_path` must be canonicalized to an absolute real path.
- `allowed_paths` must be canonicalized before comparison.
- every `allowed_path` must be inside `root_path` unless explicitly marked as an external read-only path.
- path traversal such as `..` must be resolved before policy checks.
- symlinks must be resolved before policy checks.
- symlinks that escape `root_path` are denied unless the resolved target is explicitly allowed.
- if `allowed_paths` conflicts with `root_path`, the narrower policy wins.
- missing workspace config means no project access.
- workspace ids are stable identifiers; display names are not authority.

Network policy precedence:

- task scope may further restrict workspace `network_policy`.
- task scope may not broaden workspace `network_policy` without a new approval.
- Router receives the resulting provider allowance, not the raw policy authority.

### Sandbox Semantics

The sandbox is a scoped working area under `~/.bai/sandbox/`, not a replacement for workspace permissions.

Allowed modes:

- `in_place_read`: read files directly from the workspace; no mutation.
- `in_place_mutate`: mutate approved workspace paths directly after approval.
- `sandbox_copy`: copy selected files into sandbox for experiments; no automatic writeback.
- `temp_checkout`: optional future mode for Git-backed workspaces.

Default mode:

- read-only tasks use `in_place_read`.
- approved code changes use `in_place_mutate`.
- risky experiments use `sandbox_copy`.

Writeback from sandbox to workspace is a mutation and requires Harness approval against the original workspace paths.

### Runtime State

Runtime state lives under `~/.bai/`.

Suggested runtime layout:

- `~/.bai/config/`: CLI and workspace configuration
- `~/.bai/state/`: active sessions, workflow state, approvals
- `~/.bai/logs/`: structured event logs
- `~/.bai/memory/`: session, working, and optional long-term memory stores
- `~/.bai/cache/`: model/tool/cache artifacts
- `~/.bai/sandbox/`: temporary scoped work areas

The source repository may contain tests and fixtures, but it must not become the default runtime home or default project root.

## Stable Data Contracts

These are planning schemas, not implementation code.

### `TaskRequest`

- `id`
- `source`: terminal, voice, schedule
- `execution_policy`: serial, explicit_parallel
- `intent`
- `priority`: foreground, background
- `workspace_id`
- `scope`: allowed files, allowed commands, network policy, sandbox path
- `inputs`: user text, voice transcript, file refs
- `requires_approval`: yes/no

### `ExecutionPlan`

- `id`
- `execution_policy`
- `workspace_id`
- `nodes`
- `edges`
- `artifacts`
- `checkpoints`
- `mutation_scope`
- `rollback_notes`

### `WorkflowNode`

- `id`
- `kind`: plan, code, doc, test, audit, fix, summarize, index, monitor
- `depends_on`
- `inputs`
- `outputs`
- `allowed_tools`
- `can_mutate`: yes/no
- `approval_required`: yes/no
- `retry_policy`

### `AgentInvocation`

- `agent_kind`
- `task_spec`
- `workspace_ref`
- `context_bundle`
- `model_request`
- `tool_scope`
- `output_contract`

First-slice `output_contract` values:

- `plan`: summary, ordered steps, risks, requested approval scope.
- `code_proposal`: proposed_changes, rationale, expected preimages.
- `doc`: changed docs or doc proposal, affected paths.
- `test`: command run, status, output summary, failing cases.
- `audit`: findings, severity, file/path refs, read-only confirmation.

### `AgentResult`

- `status`: success, blocked, failed
- `artifacts`
- `proposed_changes`
- `diagnostics`
- `next_checkpoint`
- `events`

### `ProposedChange`

- `path`: canonical workspace-relative path.
- `operation`: create, modify, delete.
- `content_ref`: patch artifact id or sandbox file ref.
- `expected_preimage`: file hash, modified timestamp, or absent for create.
- `rationale`
- `requires_approval`: yes/no.

### `Checkpoint`

- `id`
- `after_node`
- `approval_scope`
- `workspace_id`
- `approved_by`
- `approved_at`
- `expires_when`
- `bound_to_artifacts`
- `workspace_snapshot`

### `TaskEvent`

- `task_id`
- `event_type`: queued, started, pause_requested, paused, resume_requested, resumed, cancel_requested, completed, failed, cancelled
- `reason`
- `state_before`
- `state_after`
- `checkpoint_ref`

### `MemoryEvent`

- `layer`: session, working, long-term
- `operation`: read, write, retrieve, evict
- `scope`
- `workspace_id`
- `source_task`
- `retention`

### `LockRecord`

- `lock_id`
- `resource_type`: workspace_path, memory_scope, sandbox_path
- `resource_id`
- `owner_task_id`
- `mode`: write
- `created_at`
- `expires_at`
- `last_heartbeat_at`

Slice-one lock scope:

- only a minimal mutation guard is required.
- write locks may be represented as a single in-process "mutation in progress" flag for the first scaffold.
- no read locks.
- no model-slot locks.
- no stale lock recovery automation.
- a stale lock blocks work and asks the user to resolve it.

## Component Boundaries

### Harness

Responsibility:
Owns request intake, workspace selection, execution mode, workflow DAG, context assembly, checkpoint state, permission decisions, mutation authorization, and final result assembly.

Inputs:
User command, voice transcript, execution flags, workspace config, task context, approval decisions, permission scope.

Outputs:
ExecutionPlan, AgentInvocation, Checkpoint prompts, approved mutation requests, final response, TaskEvents.

Internal State:
Current session state, active workflow DAG, approval records, context bundle, foreground task status. Durable runtime state is stored under `~/.bai/state/`.

Explicitly Not Included:
Model selection internals, memory storage internals, background timer mechanics, agent execution internals, project discovery by crawling arbitrary directories.

Extension Points:
Workflow policy, approval policy, context assembly policy, observability hooks, future planning/reflection hooks, benchmark/eval layer, self-improvement loop.

Must Remain Outside For Now:
Autonomous self-improvement, browser control, multi-agent debate, automatic code mutation without approval.

Future benchmark/eval layer:
Attached to Harness through a replaceable adapter as an explicit triggered mode. It measures system behavior using fixed tasks, expected boundaries, latency/cost, and PolicyEngine violations. It is not a phase-one component and must not become Harness-owned evaluation logic.

Future self-improvement loop:
Attached to Harness through a replaceable adapter as a proposal-only mode. It may suggest changes based on benchmark/eval output, but must not directly modify source code, policies, memory, or routing. All proposed changes require human approval, and any future automated action must still pass through PolicyEngine.

Policy boundaries:

- workflow policy, approval policy, permission policy, context assembly policy, and mutation application should be replaceable adapters.
- Harness coordinates these policies but should not bury their rules inside one untestable control path.
- Harness owns human-facing permission decisions and approval state.
- PolicyEngine deterministically evaluates whether a proposed effect satisfies configured constraints.
- PolicyEngine must be non-bypassable for model outputs, tool calls, file operations, and memory writes.
- PolicyEngine must be replaceable.
- PolicyEngine must not depend on specific agent implementations.
- Router must not duplicate approval policy; it receives only the model/provider allowance that Harness has already authorized.

### Agents

Responsibility:
Stateless executors for bounded tasks such as planning, coding, documenting, testing, auditing, summarizing, or log analysis.

Inputs:
AgentInvocation containing task spec, scoped context, allowed tools, model request, and output contract.

Outputs:
AgentResult with artifacts, diagnostics, and proposed changes.

Internal State:
None beyond a single invocation.

Explicitly Not Included:
Persistent identity, private memory, agent-to-agent conversation, autonomous delegation, long-running control loops, applying file changes.

Extension Points:
Agent profiles, tool policies, output schemas, evaluator hooks, future self-evaluation modules.

Must Remain Outside For Now:
Emergent collaboration, recursive handoffs, automatic retry loops that mutate code.

### PolicyEngine

Responsibility:
Enforces system invariants on all actions before execution.

Inputs:
Model outputs, tool calls, file operations, memory writes.

Outputs:
allow, reject, redact, require_confirmation.

Internal State:
Configured deterministic rules and policy metadata only.

Explicitly Not Included:
LLM usage, workflow participation, agent role, task planning, model routing.

Extension Points:
Rule-based filters, pattern-based checks, future learned policy generators whose outputs compile to deterministic rules.

Must Remain Outside For Now:
Policy implementation details, dependency on PatchOptic, runtime import of PatchOptic, agent-specific behavior, runtime learned-policy inference.

### Router

Responsibility:
Selects a model according to a routing policy. The default policy is local-first, with cloud fallback only when explicitly allowed.

Inputs:
Model request, latency target, privacy level, context size, required capability, allowed providers.

Outputs:
Model choice, provider config, fallback sequence, routing reason.

Internal State:
Model registry, health/latency cache, configured provider limits.

Explicitly Not Included:
Prompt construction, memory retrieval, scheduling, workflow control, permission approval.

Extension Points:
Cost policy, latency policy, privacy policy, quality scoring, future routing strategies.

Must Remain Outside For Now:
Automatic cloud escalation without permission, complex ensemble routing, multi-model debate.

### Scheduler

Responsibility:
Orders foreground and background tasks, starts runnable work, and requests cooperative pause/cancel for preemptible background tasks.

Inputs:
TaskRequest, workspace id, priority, schedule trigger, preemption policy, lifecycle events.

Outputs:
TaskEvents, start requests, pause requests, resume requests, cancel requests.

Internal State:
Task queue, active task refs, paused task refs, schedule metadata, task leases. Durable scheduler metadata is stored under `~/.bai/state/`.

Explicitly Not Included:
Workflow DAG semantics, agent reasoning, model routing, memory implementation, mutation authority.

Extension Points:
Persistent queue, task leases, resource budgets, observability, future distributed workers.

Must Remain Outside For Now:
Cluster orchestration, complex Celery-style distributed execution, autonomous task creation.

Lock boundary:
Phase one uses a Harness-local mutation guard only. A Scheduler-owned lock manager interface is deferred until real concurrent background writes exist. Memory and sandbox adapters must not implement their own locking rules.

### Memory

Responsibility:
Provides scoped read/write/retrieve/evict operations across session, working, and optional long-term layers.

Inputs:
Memory query, write event, retrieval context, workspace id, eviction policy.

Outputs:
Relevant snippets, write acknowledgements, eviction results.

Internal State:
Layer-specific stores under `~/.bai/memory/`. The initial version may use plain files or SQLite before any vector index.

Explicitly Not Included:
Automatic reflection, sleep cycles, universal summarization, multi-stage memory graphs, context assembly decisions.

Extension Points:
Reflection, summarization, offline consolidation, compression, importance scoring, retrieval gating.

Must Remain Outside For Now:
Autonomous memory rewriting, hidden personality state, self-updating goals.

Hook boundary:
Future reflection, sleep, or consolidation hooks must run through workspace and approval boundaries. They may propose memory writes, but they must not bypass Harness-owned scope checks.

## Voice Boundary

Voice is an adapter into Harness.

Minimal responsibility:
Capture push-to-talk audio, transcribe it, pass transcript plus timing metadata to Harness, and optionally render a short terminal response.

Inputs:
Push-to-talk audio segment, terminal session id, current foreground task id.

Outputs:
Transcript, confidence/timing metadata, cancellation/interruption signal if user speaks during output.

State:
Ephemeral audio buffer only. Any transcript persisted for a workflow is stored by Harness under `~/.bai/state/` or Memory under `~/.bai/memory/`.

Explicitly Not Included:
Always-on listening, wake word detection, GUI, browser WebRTC, cloud realtime sessions, voice agent handoffs.

Extension Points:
Alternative STT engine, VAD, TTS, interruption handling, cloud realtime fallback.

Voice interruption mapping:

- while foreground output is streaming, user speech creates an interrupt event for the active foreground task.
- interrupting a foreground task pauses output generation but does not cancel mutations already in progress.
- interrupting a background task maps to Scheduler `pause_requested`.
- explicit spoken cancellation maps to Scheduler `cancel_requested` once the transcript is confirmed.
- mutation steps remain governed by Harness approval and cannot be cancelled by voice alone after the mutation tool has started unless the tool supports cooperative cancellation.

## Mutation Authority

Only the Harness authorizes mutation.

Agents may propose changes. They do not apply them by authority of their own role.

Mutation flow:

1. Agent returns `proposed_changes`.
2. Harness sends the agent output through PolicyEngine.
3. PolicyEngine returns allow, reject, redact, or require_confirmation.
4. Harness checks approved `mutation_scope`.
5. Harness asks for approval if scope is missing, expired, too broad, or required by PolicyEngine.
6. Harness invokes the allowed tool to apply changes.
7. Harness records a TaskEvent and binds the result to the checkpoint.

Permission enforcement belongs at the Harness/tool boundary, not inside prompts alone.

Effect check:
Every tool call, file operation, and memory write must pass through PolicyEngine immediately before execution, even if the final agent output was already checked.

Mutation tool adapter:

- narrow API: validate preimage, apply ProposedChange, return applied artifact or failure.
- no model calls.
- no policy decisions.
- no workspace discovery.
- no approval prompts.
- receives only Harness-approved ProposedChange objects.

Approved scope must include:

- workspace id
- workspace root
- workspace snapshot when available: branch, commit, file hashes, or modified timestamps
- allowed paths
- allowed commands
- network access policy
- destructive action policy
- sandbox path
- expiry condition
- approving human

Workspace snapshot minimum:

- Git workspace: current branch and commit are required; dirty file paths are recorded when relevant.
- non-Git workspace: file hashes or modified timestamps are required for approved mutation targets.
- read-only approvals may bind to artifact ids instead of full file snapshots when no mutation follows.

Approval invalidation:

- approval expires when `expires_when` is reached.
- approval is invalid if workspace id or canonical root changes.
- approval is invalid if the target branch/commit changes and the approval was bound to Git state.
- approval is invalid if bound input artifacts change.
- approval is invalid if approved file hashes or modified timestamps change before mutation.
- approval is invalid if the requested command, path, network access, or sandbox mode exceeds the approved scope.
- invalid approval returns to the checkpoint instead of auto-expanding scope.

## Context Assembly

Harness owns context assembly.

Context bundle sources:

- user request
- selected workspace config
- active workflow artifacts
- approved memory snippets
- file excerpts within scope
- Scheduler task metadata
- Router constraints

Memory retrieves candidates only. Harness decides what enters the AgentInvocation.

Router receives model requirements only. It does not inspect or assemble prompts.

Agents consume the final context bundle. They do not fetch unrelated context unless explicitly given a scoped tool.

## Memory System

Memory is one modular subsystem example. The same pattern should apply later to planning, evaluation, routing, observability, and tool policy.

### Layers

Session:
Ephemeral per interactive session. Holds recent turns, current foreground task state, and temporary voice context.

Working:
Task-scoped memory for the active workflow. Holds plan decisions, approvals, constraints, file references, and known risks.

Long-Term:
Optional durable indexed storage for user-approved project facts and repo summaries. It is not required for the first vertical slice.

Layer separation:

- session memory is keyed by session id
- working memory is keyed by task id and workspace id
- long-term memory is keyed by memory scope and workspace id
- no memory layer may infer project roots outside configured workspaces

### Minimal Interface

- `read(query, context)`: return session or working memory relevant to a specific execution context.
- `write(event)`: store an explicit event or approved note.
- `retrieve(query)`: query long-term indexed memory only if that layer is explicitly enabled later.
- `evict(policy)`: remove or compact memory according to a declared policy.

This is an interface only. No complex memory logic should be implemented in the first version.

### Extension Hooks

Future pluggable hooks:

- reflection / summarization
- sleep / offline consolidation
- memory compression
- importance scoring
- retrieval gating
- privacy filters
- stale-memory detection

These hooks must be optional modules, not hardcoded behavior.

### What Not To Do

- Do not auto-summarize everything.
- Do not build complex pipelines.
- Do not create multi-stage memory graphs.
- Do not let agents privately mutate memory.
- Do not treat memory as the only future-facing subsystem.

## Execution Model

Execution path:

Agent -> PolicyEngine -> Execution

PolicyEngine is not a workflow step. It enforces deterministic constraints before tool calls, file operations, memory writes, or other execution effects.

### Serial DAG Workflow

The developer workflow is represented as a DAG even when the common path looks linear.

Minimal common graph:

- `plan`
- `code`, depends on `plan_approved`
- `doc`, depends on `code`
- `unit_test`, depends on `code`
- `audit`, depends on `doc` and `unit_test`
- `fix`, depends on `audit` when issues exist

This allows fan-in at `audit` without parallel mutation. `doc` and `unit_test` may run only after code mutation is complete. They may be serial by default even if the graph permits independent branches later.

Node semantics:

- nodes consume named artifacts
- nodes produce named artifacts
- edges bind artifact availability, not just order
- checkpoints bind approval to a node output and mutation scope
- failed nodes can be retried without re-running completed dependencies unless artifacts are invalidated

Retry policy:

- default max attempts: 1 retry after initial failure
- retryable: model timeout, transient command failure, test failure with bounded fix scope
- not retryable without approval: permission denial, out-of-scope mutation, destructive command, cloud escalation
- retries must reuse prior approved artifacts unless the checkpoint is reopened

Rollback policy:

- first implementation records rollback notes and changed paths
- destructive rollback is never automatic
- later implementation may use patch-level reversal with explicit approval

### Parallel Model

Parallel execution is only:

fan-out -> independent workers -> aggregation

Allowed for read-only or isolated tasks such as summarization, comparison, log review, and read-only audit.

Strict rules:

- no agent-to-agent conversations
- no emergent chat loops
- no parallel code mutation
- no shared writable paths across workers
- aggregation is handled by Harness
- human approval is still required for mutations

Parallel safety requirements:

- each worker receives an explicit read scope
- write scope is empty unless the task is an isolated background index write
- shared resources require a named lock
- aggregation must treat worker outputs as untrusted until checked
- audit is parallel only when read-only

Lock semantics:

- slice one uses only the minimal mutation guard needed to prevent concurrent writes.
- full lock identity, ownership, expiry, heartbeat, stale handling, and queue behavior are later-only.

## Scheduler Model

### Priority Tiers

Foreground:
Voice interaction, terminal commands, active developer workflow, user-visible requests.

Background:
Repo indexing, trading-system monitoring, log summarization, scheduled jobs.

Slice-one background scope:
Only one toy cooperative background task is required, such as counting files or scanning a small fixture list with periodic pause checks. Real repo indexing, trading monitoring, and scheduled jobs are later uses of the same lifecycle contract.

Ordering:

- foreground tasks are FIFO unless the user explicitly interrupts
- background tasks are FIFO within task type
- a paused background task resumes before newer background work
- foreground work may starve background work by design, but paused background tasks should be reported visibly

### Cooperative Preemption Contract

Preemption is cooperative, not process-level magic.

Background tasks must periodically check for:

- pause requested
- cancel requested
- lease expired
- foreground task waiting

Checkpoint protocol:

- background task writes progress marker before pausing
- task reports last completed unit
- task releases writable locks before entering paused
- resume starts from the last completed unit, not from arbitrary stack state

Progress marker contract:

For phase one, the toy cooperative task may use a minimal marker with only task id, last completed unit, and updated_at. The fuller contract below is for later resumable background tasks.

Later-only full marker contract:

- marker lives under `~/.bai/state/progress/<task_id>.json`.
- marker unit is task-defined but must be deterministic, such as file path, log offset, repo commit plus path cursor, or schedule window.
- marker records workspace id, task kind, last completed unit, input snapshot, updated_at, and invalidation keys.
- marker is invalid if workspace id changes, input snapshot changes, task kind changes, or referenced files/logs are missing.
- stale marker causes restart from the beginning of the task, not partial resume.

State ownership:

- Scheduler owns lifecycle state
- Harness owns workflow/checkpoint state
- task implementation owns progress markers

### Lifecycle

Durable states:

- pending
- running
- paused
- completed
- failed
- cancelled

Events:

- queued
- started
- pause_requested
- paused
- resume_requested
- resumed
- cancel_requested
- completed
- failed
- cancelled

`resumed` is an event. After resume, durable state returns to `running`.

Valid transitions:

- pending -> running
- running -> paused
- paused -> running
- running -> completed
- running -> failed
- pending -> cancelled
- running -> cancelled
- paused -> cancelled

Foreground tasks should not wait for background tasks unless explicitly requested.

## Minimal CLI Surface

The global command is `bai`.

First-slice commands:

- `bai`: start interactive terminal session.
- `bai --workspace <name-or-id>`: start interactive session bound to a configured workspace.
- `bai workspace list`: list configured workspaces.
- `bai workspace add <name> <root_path>`: add a workspace with conservative defaults.
- `bai workspace show <name-or-id>`: show resolved workspace config and allowed paths.
- `bai run --workspace <name-or-id> "<request>"`: run one foreground request.

Later commands, only when background task control is implemented:

- `bai tasks`: show foreground/background task state.
- `bai pause <task_id>`: request cooperative pause.
- `bai resume <task_id>`: request resume.
- `bai cancel <task_id>`: request cancellation.

Not in first slice:

- daemon installation
- shell completion
- remote server mode
- project auto-discovery
- global file search outside configured workspaces
- background task control commands unless background control is part of the slice

## Minimal Tech Stack

### Selected For First Slice

Ollama:
Local-first model execution for small and medium local models.

LiteLLM:
Use only if direct Ollama/cloud adapters become messy. It is useful for a unified interface, but not mandatory in the first slice.

### Conditional

Whisper:
Local speech-to-text for SSH push-to-talk. Postpone until the terminal text path proves the Harness contracts.

LangGraph:
Useful for durable DAG workflows and human-in-the-loop checkpoints. Postpone until the hand-written DAG contract becomes painful.

APScheduler:
Useful for recurring background jobs. Postpone until one real scheduled job exists.

Chroma:
Useful for long-term indexed memory. Postpone until long-term retrieval is proven necessary. Validate the Memory interface first with plain files or SQLite.

### Postponed

FAISS:
Postpone until vector performance matters.

Celery:
Postpone until background tasks need distributed workers or durable queues beyond a single local process.

Cloud realtime voice APIs:
Postpone unless local Whisper latency is insufficient.

Complex observability stack:
Postpone. Start with structured events and logs.

## Repository Scaffolding Plan

The `bai-harness` repository contains source code, tests, and docs only. It is not the parent directory for user projects and it is not the runtime data directory.

### `harness/`

Purpose:
Core orchestration source code for workspace selection, execution mode, workflow state, context assembly, checkpoints, mutation authorization.

Later:
Workflow runners, permission gates, event logging adapters.

Not Yet:
Runtime session files, user project files, large framework wrappers, autonomous planners, browser automation.

### `agents/`

Purpose:
Source code for stateless task executors.

Later:
Planner, coder, doc writer, tester, auditor, summarizer.

Not Yet:
Persistent agent identities, agent chat rooms, recursive delegation, agent-owned workspace state.

### `policy/`

Purpose:
PolicyEngine interface plus the minimal non-bypassable phase-one gate.

Later:
Rule adapters, pattern checks, learned policy adapters.

Not Yet:
Large rule library, PatchOptic dependency, LLM-based policy logic.

### `router/`

Purpose:
Source code for model selection and fallback policy.

Later:
Model registry, latency cache, cost/privacy policies.

Not Yet:
Runtime model cache, model ensembles, benchmark automation, automatic cloud escalation.

### `scheduler/`

Purpose:
Source code for foreground/background task queue, lifecycle state, cooperative preemption.

Later:
APScheduler integration, task persistence, pause/resume hooks.

Not Yet:
Runtime queue files inside the repo, distributed workers, Celery, cluster scheduling.

### `memory/`

Purpose:
Source code for the memory interface and simple storage adapters.

Later:
Session store, working store, optional indexed long-term store.

Not Yet:
Actual user memory files inside the repo, reflection, sleep, compression, importance scoring.

### `voice/`

Purpose:
Source code for push-to-talk terminal voice adapter.

Later:
Audio capture adapter, Whisper transcription adapter.

Not Yet:
Persisted audio inside the repo, GUI, browser voice, always-on listening, realtime cloud sessions.

### `cli/`

Purpose:
Source code for the global `bai` command.

Later:
Command parsing, workspace selection, foreground/background task commands.

Not Yet:
Shell-specific installers, daemon managers, project-local runtime state.

### `docs/`

Purpose:
Project plan, resources, architecture notes.

Later:
Implementation notes, ADRs, operating guide.

Not Yet:
Generated reference manuals or broad design essays.

### `tests/`

Purpose:
Focused tests for workflow contracts, router decisions, scheduler transitions, memory boundaries, and mutation authorization.

Later:
Integration tests for local model routing and background preemption.

Not Yet:
Large benchmark suites.

## User-Level Runtime Plan

Runtime directories are created by the `bai` CLI under `~/.bai/`.

### `~/.bai/config/`

Purpose:
User-level config, workspace registry, provider settings, permission defaults.

Not In Repo:
Secrets, local machine paths, workspace registry.

### `~/.bai/state/`

Purpose:
Active sessions, workflow DAG state, approvals, task lifecycle state.

Not In Repo:
Session state, checkpoint records, approval records.

### `~/.bai/logs/`

Purpose:
Structured runtime logs and task events.

Not In Repo:
Personal runtime logs.

### `~/.bai/memory/`

Purpose:
Session, working, and optional long-term memory stores.

Not In Repo:
User memory, indexed project summaries, private notes.

### `~/.bai/cache/`

Purpose:
Reusable local cache for model/tool outputs when allowed.

Not In Repo:
Runtime cache artifacts.

### `~/.bai/sandbox/`

Purpose:
Temporary scoped work areas for safe operations against configured workspaces.

Not In Repo:
Scratch files, temporary copies, sandbox outputs.

## First Implementation Target

A minimal vertical slice should prove:

Phase-one scaffolding should implement only the contracts needed for this slice. The broader data-contract section is a boundary map, not a requirement to build every record type immediately.

1. Terminal text enters Harness first; push-to-talk may use the same path after the text path works.
2. Harness chooses serial mode by default.
3. Harness resolves an explicit workspace from `~/.bai/config/`.
4. Harness assembles a scoped context bundle from that external workspace.
5. Router selects a local model.
6. One stateless Agent performs a bounded Plan task.
7. PolicyEngine gates the Agent output and one proposed effect before execution.
8. Harness records approval for the Plan artifact under `~/.bai/state/`.
9. Harness authorizes one scoped Code mutation in the configured external workspace after approval.
10. Memory interface supports session and working writes under `~/.bai/memory/` without vector storage.
11. Scheduler can transition one toy cooperative background task from running to paused.

No broader system should be added until this works.
