# Modernizing the AI Orchestration Layer — LangGraph Migration

> This document summarizes the content for the visual presentation in `langgraph-migration.html` (to be generated after review and approval of this document). Open the HTML in a browser for the full slide design.

---

## Executive Summary

The Workload Analyzer AI assistant is built on a custom orchestration engine written and maintained entirely in-house. That engine works well today, but it is becoming a ceiling — not a foundation — as AI workloads grow in complexity.

This proposal replaces **only the AI orchestration layer** with LangGraph, an industry-standard agent framework maintained by LangChain Inc. Everything users see and interact with — the chat interface, authentication, feedback, admin, and knowledge base — remains unchanged.

**Expected outcomes:**

- Elimination of in-house maintenance burden on core AI plumbing
- Persistent conversation history that survives process restarts (zero additional infrastructure)
- Durable batch execution: failed multi-job analyses resume from the point of failure instead of restarting from scratch
- Human-in-the-loop checkpoints: AI can pause and ask for confirmation before executing consequential actions
- Observability via LangSmith: full trace visibility into every AI decision, tool call, and reasoning step
- Multi-model and multimodal support as a free byproduct, without new parsers to maintain

---

## Current State — What We Built

The current AI layer is a fully custom stack:

| Component             | Role                                                                                            | Owned By |
| --------------------- | ----------------------------------------------------------------------------------------------- | -------- |
| `ChatManager`         | Orchestrates the conversation loop — calls the LLM, runs tool calls, handles errors             | Us       |
| `BedrockClient`       | Transport layer — formats messages, sends to AWS Bedrock, parses responses                      | Us       |
| `ToolManager`         | Generates tool definitions from the FastAPI OpenAPI spec; executes tool HTTP calls              | Us       |
| `MessagePreprocessor` | 4-stage pipeline that prevents conversation history from overflowing the model's context window | Us       |
| `SessionManager`      | Tracks active chat sessions and conversation history in memory                                  | Us       |

This architecture was intentionally designed to be modular and self-contained. It has served its purpose well. The natural next step is to replace the custom internals with a mature, maintained framework, while keeping the surrounding application (UI, auth, feedback, admin) intact.

---

## The Problem — Why Change Now

### Maintenance ceiling

Every capability we want to add to the AI layer requires writing new code in our in-house components:

- Want the AI to run analysis steps in parallel? Requires redesigning `ChatManager`.
- Want conversation history to persist across server restarts? Requires a new database-backed `SessionManager`.
- Want tracing and step-by-step visibility for debugging? Requires building an observability pipeline.
- Want the AI to pause and ask for user confirmation? No mechanism exists today.

Each of these is a significant engineering investment in infrastructure that is not our core product.

### Growing gap with the industry

The AI orchestration space has consolidated. LangGraph has become the standard for production agent systems:

| Capability                                | Today (custom)                                                                                                | Industry (LangGraph)                           |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| Multi-step tool calling                   | ✅ Up to 10 rounds                                                                                            | ✅ Unlimited graph loops                       |
| Session persistence                       | ❌ In-memory, lost on restart                                                                                 | ✅ Postgres-backed, durable                    |
| Parallel sub-tasks                        | ❌ Not possible                                                                                               | ✅ Native subgraphs                            |
| Durable execution (resume on failure)     | ❌ Not possible                                                                                               | ✅ Core primitive                              |
| Human-in-the-loop (confirm before acting) | ❌ Not possible                                                                                               | ✅ Core primitive                              |
| Tracing and observability                 | ❌ Basic Python logging                                                                                       | ✅ LangSmith — full step traces                |
| Multi-model support                       | ⚠️ Requires new parser per model                                                                              | ✅ All models via LangChain abstraction        |
| Active maintenance                        | ⚠️ Maintained exclusively by this team — all bug fixes and new capabilities require internal engineering time | ✅ LangChain Inc + large open-source community |

### The batch analysis failure case

The most concrete near-term impact is batch analysis. When a user asks the AI to analyze a 10-child-job workload, the current system processes jobs sequentially. If it fails on job 7 — due to a network timeout, a Bedrock rate limit, or a service hiccup — the entire analysis restarts from the beginning. With LangGraph's durable execution, the graph checkpoints after every job and resumes from job 7 automatically.

---

## Why LangGraph

The selection is not arbitrary. LangGraph was evaluated against five decision criteria that directly reflect the constraints and goals of this project:

### 1. Infrastructure fit — zero new dependencies

The existing stack is FastAPI + Postgres + AWS Bedrock (Claude). LangGraph maps directly onto all three:

- `langchain-aws` provides `ChatBedrockConverse` — same Claude model, same Bedrock endpoint, same AWS region, no new credentials or infrastructure
- `langgraph-checkpoint-postgres` uses the existing Postgres instance with a dedicated `langgraph_*` schema — no new database, no new service
- LangGraph runs as an async Python library inside FastAPI — no sidecar, no separate process, no new deployment unit

No other framework in the space has a checkpointer that maps directly to an already-provisioned Postgres instance.

### 2. Component-level migration path

The existing five-component architecture (`ChatManager`, `BedrockClient`, `ToolManager`, `MessagePreprocessor`, `SessionManager`) has a direct one-to-one mapping to LangGraph primitives. There is no conceptual gap to bridge and no forced redesign. Each component is replaced in isolation, phase by phase, without touching the surrounding application.

Frameworks like Amazon Bedrock Agents or AutoGen require adopting a new top-level execution model that does not map onto the existing architecture — they are a replacement, not a migration.

### 3. Capabilities that are core primitives, not workarounds

Durable execution, human-in-the-loop, and parallel subgraphs are first-class concepts in LangGraph's graph execution model. In every alternative evaluated, these either do not exist or require significant custom scaffolding to approximate:

- **Amazon Bedrock Agents**: no portable graph definition, no HITL primitives, resumption not supported
- **AutoGen / CrewAI**: durable execution and Postgres checkpointing are not available; HITL requires custom state management
- **Semantic Kernel**: primarily Microsoft Azure-centric; no production-grade Postgres checkpointer; weaker AWS Bedrock integration

### 4. Observability and token tracking included

LangSmith is LangGraph's native observability layer. It provides full step traces, token usage per call, latency per node, and tool call audit trails — without any custom instrumentation. This directly addresses two standing requirements: debugging visibility and token consumption reporting.

No other evaluated framework includes comparable observability at this level of integration.

### 5. Production maturity and commercial backing

LangGraph is maintained by LangChain Inc, has the largest production deployment base of any Python agentic framework as of 2026, and is actively developed. The project is not a research prototype or a vendor-specific managed service — it is an open-source library with commercial support available.

This matters for a tool that will continue to evolve: bugs get fixed upstream, new model integrations are added without code changes on our side, and the framework's design decisions are informed by production feedback at scale.

---

## The Proposal — Surgical Replacement

Replace the five internal components with LangGraph equivalents. Touch nothing else.

### What changes

| Current Component                           | Replaced With                                    | Why                                                             |
| ------------------------------------------- | ------------------------------------------------ | --------------------------------------------------------------- |
| `ChatManager` (conversation loop)           | LangGraph `StateGraph`                           | Branching, parallel steps, checkpointing, HITL                  |
| `BedrockClient` (LLM transport)             | `ChatBedrockConverse` via `langchain-aws`        | Same Claude model, no new AWS costs, vendor-maintained          |
| `ToolManager` (tool execution)              | LangGraph `ToolNode` + explicit tool definitions | Testable, composable, full control                              |
| `MessagePreprocessor` (context budget)      | Custom preprocessing node in the graph           | Preserves existing multi-stage logic, now as a graph node       |
| `SessionManager` (session lifecycle)        | `langgraph-checkpoint-postgres`                  | Durable, persistent, zero new infrastructure                    |
| `BEDROCK_*` environment variables (81 vars) | `AUTOCHAT_*` prefix                              | Removes the false AWS coupling; clean rename, no legacy aliases |

### What does NOT change

| Component                                             | Status                                                               |
| ----------------------------------------------------- | -------------------------------------------------------------------- |
| Chat UI (`chat.html`, `chat-client.js`, `styles.css`) | Untouched — it talks to a WebSocket endpoint, not to the AI directly |
| WebSocket transport layer                             | Minimal adapter — calls the LangGraph graph instead of `ChatManager` |
| SSO / OAuth2 PKCE authentication                      | Completely independent of the AI layer                               |
| Feedback collection and continuous learning loop      | Completely independent of the AI layer                               |
| Admin API and dashboard                               | Completely independent of the AI layer                               |
| Preset prompts                                        | UI concern — unchanged                                               |
| Knowledge base (pgvector / RAG)                       | Injected into the LangGraph graph as a retrieval node                |
| AWS Bedrock infrastructure                            | Same endpoint, same model (`us.anthropic.claude-sonnet-5`), same region         |
| Existing Postgres database                            | Now also used for conversation checkpointing — no new DB             |

### The WebSocket adapter

The single integration point is the WebSocket handler. Today:

```
User message → WebSocketHandler → ChatManager → BedrockClient
```

After migration:

```
User message → WebSocketHandler → LangGraph graph (streamed events) → Client
```

The WebSocket message protocol (`ai_response`, `typing`, `tool_call`, `error`, etc.) stays identical. The client JavaScript does not change.

### Configuration namespace rename

All 81 `BEDROCK_*` environment variables will be renamed to `AUTOCHAT_*` as part of Phase 1. As the sole deployment, this is a clean rename with no legacy aliases — simpler code, no dead paths to maintain. A one-time find-and-replace in the deployment's `.env` file is all that is required.

---

## New Capabilities Unlocked

### Durable batch execution

Today: 10-job analysis fails on job 7 → restart from job 1, all progress lost.

With LangGraph:

1. Graph checkpoints state after each job completes
2. Failure on job 7 → resume from job 7 on retry
3. Zero lost work; fully transparent to the user

### Human-in-the-loop

Enables AI flows like:

> "I identified 3 performance bottlenecks across the workload. Before I generate the full optimization report, do you want me to proceed?"

The AI pauses at a defined checkpoint, waits for user confirmation, then continues — or adjusts based on feedback. Not possible in the current linear loop.

### Parallel sub-analysis

For multi-job workloads, child jobs can be analyzed concurrently using LangGraph subgraphs, eliminating the sequential bottleneck where each job waits for the prior one to complete.

### Full observability with LangSmith

Every AI response becomes inspectable: which tools were called, in what order, what data was passed, how long each step took, where errors occurred. Debugging a bad AI response goes from "read Python logs and guess" to "click the trace in LangSmith and see exactly what happened."

### Token consumption tracking

This has been a standing request with no clean solution in the current architecture. Today, token counts are parsed per-response for some models but are never aggregated, stored, or reported anywhere.

With LangGraph and `langchain-aws`, token usage (`input_tokens`, `output_tokens`, `total_tokens`) is available on every LLM call via standard `AIMessage.usage_metadata`. LangSmith automatically aggregates these per run, per session, and per user. This enables:

- Per-user and per-session consumption dashboards
- Cost attribution per job analysis or batch run
- Alerting when a single session exceeds a token budget
- Historical trend data for capacity planning

No custom instrumentation required — this is captured by the framework as a byproduct of using `ChatBedrockConverse`.

---

## Migration Strategy — Phased and Low-Risk

### Phase 1 — Foundation (2–3 weeks)

Scope: Set up LangGraph dependencies, define the core `StateGraph`, implement the preprocessing node, wire the WebSocket adapter. Rename all 81 `BEDROCK_*` environment variables to `AUTOCHAT_*`.

Deliverable: Existing chat functionality works identically on the new graph. No visible change to users. One-time `.env` update required on deployment.

Risk: Low — the graph is a drop-in replacement for `ChatManager`. Rollback is a one-line change.

### Phase 2 — Tool Migration (1–2 weeks)

Scope: Replace `ToolManager`'s HTTP-based tool execution with explicit LangGraph `ToolNode` definitions for the handful of API endpoints the AI actually calls (`/jobs`, `/platforms`, `/health`, file download).

Deliverable: Tool calling works via the graph. Auto-generation from OpenAPI is preserved as a utility that produces LangGraph-compatible tool definitions.

Risk: Low — tool definitions map directly to existing ISS client methods.

### Phase 3 — Persistence (1 week)

Scope: Replace in-memory `SessionManager` with `langgraph-checkpoint-postgres` using the existing Postgres instance.

Deliverable: Conversation history survives process restarts. Sessions are no longer lost during deployments.

Risk: Low — checkpointer is an additive change; existing session behavior is preserved.

### Phase 4 — Batch Durability (2–3 weeks)

Scope: Wrap the `batch_processor` workflow in a LangGraph graph with per-job checkpointing.

Deliverable: Batch analyses resume from the point of failure. Human-in-the-loop confirmation available before report generation.

Risk: Low — existing batch logic moves into graph nodes without behavioral change.

---

## Timeline Summary

| Phase                     | Scope                                                | Duration  | User-Visible Impact                    |
| ------------------------- | ---------------------------------------------------- | --------- | -------------------------------------- |
| Phase 1: Foundation       | Graph wiring, preprocessing node, WebSocket adapter  | 2–3 weeks | None                                   |
| Phase 2: Tools            | LangGraph tool definitions, preserve auto-generation | 1–2 weeks | None                                   |
| Phase 3: Persistence      | Postgres checkpointer, durable sessions              | 1 week    | Sessions persist across restarts       |
| Phase 4: Batch Durability | Durable batch execution, HITL checkpoints            | 2–3 weeks | Batch analyses no longer lose progress |

Total: **6–9 weeks** for a fully migrated, durability-enabled AI layer.

---

## Risks and Mitigations

| Risk                                                        | Likelihood | Mitigation                                                                             |
| ----------------------------------------------------------- | ---------- | -------------------------------------------------------------------------------------- |
| LangGraph API changes break our graph                       | Low        | Pin to a stable release; LangChain Inc maintains backward compat                       |
| `MessagePreprocessor` logic is too complex to port          | Low        | It becomes a standalone graph node — existing logic unchanged, new location            |
| Streaming behavior differs from current                     | Low        | LangGraph streaming is richer than current; WebSocket protocol is unchanged            |
| Tool behavior regression                                    | Low        | Existing tool integration tests cover this; Phase 2 includes full regression run       |
| Postgres checkpointer schema conflicts with existing tables | Low        | Uses dedicated `langgraph_*` schema prefix, no conflicts                               |
| LangSmith data privacy (traces sent externally)             | Medium     | LangSmith is opt-in; can run in fully offline mode with local trace logging            |
| Framework overhead adds per-response latency                | Low        | Negligible for I/O-bound workloads; async graph execution matches current async design |

---

## What We Are Not Doing

To be explicit about scope:

- **No UI changes** — the chat interface, admin dashboard, and preset prompts are not touched.
- **No auth changes** — SSO, OAuth2, and bearer token flows are unaffected.
- **No new AWS infrastructure** — same Bedrock endpoint, same model, same region.
- **No new database** — Postgres checkpointer uses the existing database instance.
- **No big-bang rewrite** — each phase is independently deployable and independently testable.

---

## Alternatives Considered

| Alternative                                | Reason Not Selected                                                                                     |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------- |
| Keep current architecture and add features | Each new capability requires significant custom development; maintenance burden grows                   |
| Amazon Bedrock Agents                      | No portable graph definition; no HITL primitives; no durable execution; harder to debug; vendor lock-in |
| AutoGen / CrewAI                           | Less production-proven; no Postgres checkpointer; HITL and durability require custom scaffolding        |
| Semantic Kernel                            | Microsoft/Azure-centric; weaker AWS Bedrock integration; no production-grade Postgres checkpointer      |
| Full rewrite from scratch                  | Maximum risk; zero continuity; team already familiar with LangChain ecosystem                           |

LangGraph is the selection because it is the only option that satisfies all five criteria simultaneously: zero new infrastructure, a direct component migration path, first-class primitives for every required capability, built-in observability with token tracking, and production maturity with commercial backing.

---

## Recommendation

Approve Phase 1 and Phase 2 immediately as a single 3–5 week sprint. This delivers the foundation with zero user-visible risk and unlocks all subsequent phases.

Phases 3 and 4 follow as a second sprint once Phase 1–2 are validated in production. Phase 4 (batch durability) is the highest business-value delivery: it directly resolves the production failure mode where long-running batch analyses lose all progress on error.

The total investment is **6–9 weeks of engineering time** to eliminate an ongoing maintenance burden, gain industry-standard observability, and unlock capabilities (durable execution, parallel analysis, human-in-the-loop) that the current architecture fundamentally cannot provide.

**Success criteria:**

- Phase 1: Existing test suite passes on the new graph with no regression in response quality; `.env` updated on deployment; chat functionality is user-transparent.
- Phase 4: A batch analysis that previously failed mid-run completes via resume from the checkpoint rather than restarting from scratch.
