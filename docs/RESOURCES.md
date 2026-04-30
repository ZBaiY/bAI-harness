# bAI Resources

High-signal references for a minimal personal AI harness. Resources are grouped by implementation pressure: use the first group early, treat later groups as cautions or future references.

## Start Here

### Anthropic: Building Effective Agents

Practical 2024 guidance favoring simple composable workflows before complex autonomous agents. Best alignment with the "minimal first" rule.

Link: https://www.anthropic.com/research/building-effective-agents/

### PatchOptic Conceptual Overview

Optional local read-only reference for deterministic constraint layers, state isolation, write scopes, and contamination prevention. Conceptual reference only; bAI must not depend on PatchOptic at runtime, and this path may not exist on every machine.

Local path: `../PatchOptic/bai_read/CONCEPTUAL_OVERVIEW.md`

### Ollama API Documentation

Local model runtime API for local-first execution.

Link: https://docs.ollama.com/api

### OpenAI Whisper

Local speech recognition model and reference implementation for the push-to-talk path.

Link: https://github.com/openai/whisper

### Introducing Whisper

Background on Whisper's ASR design and robustness.

Link: https://openai.com/research/whisper/

## Workflow And Orchestration

### LangGraph Overview

Low-level orchestration framework focused on durable execution, streaming, and human-in-the-loop workflows. Use only if the hand-written DAG/checkpoint contract becomes too costly.

Link: https://docs.langchain.com/oss/python/langgraph/overview

### LangGraph Durable Execution

Useful for checkpointed DAG execution, resumability, and human approval pauses.

Link: https://docs.langchain.com/oss/python/langgraph/durable-execution

### OpenAI Agents SDK Guardrails

Useful reference for guardrail concepts around inputs, outputs, and tools. Do not treat this as a complete permission model because workflow and tool boundary placement still needs local enforcement.

Link: https://openai.github.io/openai-agents-python/guardrails/

## Model Routing

### LiteLLM Documentation

Unified interface for multiple model providers, including fallback and routing behavior. Useful once direct local/cloud adapters start to duplicate logic.

Link: https://docs.litellm.ai/

## Benchmark And Eval

These are future-facing references for triggered measurement. They should not pressure phase-one scaffolding.

### OpenAI Evals

Framework and registry for evaluating LLMs and LLM systems. Useful as a reference for fixed eval cases and regression reporting.

Link: https://github.com/openai/evals

### Inspect AI

Open-source evaluation framework from the UK AI Security Institute. Useful as a reference for structured evaluation tasks and scoring.

Link: https://inspect.aisi.org.uk/

### SWE-bench

Benchmark for evaluating software engineering agents on real GitHub issues. Useful later for understanding issue-to-patch evaluation, not a v1 target.

Link: https://www.swebench.com/SWE-bench/

### Reflexion: Language Agents with Verbal Reinforcement Learning

Future-facing self-improvement reference. Use only as a cautionary design input: bAI may propose changes from eval feedback, but must not self-modify automatically.

Link: https://arxiv.org/abs/2303.11366

## Memory Architectures

These are future-facing references, not design anchors for the first slice.

### A Survey on the Memory Mechanism of Large Language Model based Agents

2024 survey covering memory mechanisms, design patterns, evaluation, and open problems for LLM agents.

Link: https://arxiv.org/abs/2404.13501
PDF: https://arxiv.org/pdf/2404.13501
Local path: `docs/papers/2404.13501-llm-agent-memory-survey.pdf`

### MemGPT: Towards LLMs as Operating Systems

Introduces virtual context management and tiered memory concepts for long-running LLM interactions. Useful as a boundary-setting reference, not a v1 target.

Link: https://arxiv.org/abs/2310.08560
PDF: https://arxiv.org/pdf/2310.08560
Local path: `docs/papers/2310.08560-memgpt.pdf`

### MemInsight: Autonomous Memory Augmentation for LLM Agents

2025 paper on improving long-term memory representation and retrieval for agents. Future context only.

Link: https://arxiv.org/abs/2503.21760
PDF: https://arxiv.org/pdf/2503.21760
Local path: `docs/papers/2503.21760-meminsight.pdf`

### Generative Agents: Interactive Simulacra of Human Behavior

Classic memory, reflection, and planning architecture. Useful mainly as a caution against overbuilding memory/reflection in bAI v1.

Link: https://arxiv.org/abs/2304.03442
PDF: https://arxiv.org/pdf/2304.03442
Local path: `docs/papers/2304.03442-generative-agents.pdf`

### Chroma Documentation

Local/self-hosted vector storage with metadata filtering and retrieval primitives. Postpone until long-term indexed retrieval is proven necessary.

Link: https://docs.trychroma.com/

## Task Scheduling

### APScheduler 3.x Documentation

Stable in-process Python scheduler reference for recurring local jobs. Prefer version-pinned 3.x behavior for initial implementation rather than unreleased master behavior.

Link: https://apscheduler.readthedocs.io/en/3.x/

### APScheduler Master User Guide

Useful for understanding emerging Task/Schedule/Job concepts, but may track unreleased behavior and should not drive v1 contracts.

Link: https://apscheduler.readthedocs.io/en/master/userguide.html

### Celery Documentation

Distributed task queue reference. Keep postponed unless local scheduling is insufficient.

Link: https://docs.celeryq.dev/

## Voice Interfaces

### OpenAI Voice Agents Guide

Useful reference for low-latency voice architecture tradeoffs. Cloud realtime voice is postponed for bAI v1.

Link: https://platform.openai.com/docs/guides/voice-agents

### OpenAI Realtime API Voice Design

Reference for future realtime voice if local push-to-talk is not enough.

Link: https://platform.openai.com/docs/guides/realtime/voice-design
