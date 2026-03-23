# KB Architecture Comparison — RAG vs Tool Calling vs Hybrid

> 📊 This document summarizes the interactive visual diagrams in [`kb-architecture-diagrams.html`](../kb-architecture-diagrams.html). Open that file in a browser for the full visual comparison.

---

## Overview

When integrating a custom knowledge base into the AI assistant, three architectural approaches are available:

| Approach         | Summary                                                                  |
| ---------------- | ------------------------------------------------------------------------ |
| **RAG**          | Auto-retrieves relevant chunks on every message                          |
| **Tool Calling** | AI explicitly calls KB search endpoints when needed                      |
| **Hybrid**       | Combines both: RAG for reliability + Tool Calling for structured queries |

---

## RAG — Retrieval-Augmented Generation

Every user message triggers an automatic semantic similarity search. Top-K chunks are injected into the LLM context before the response is generated.

**Strengths:**

- KB context always available — no missed lookups
- Semantic search handles ambiguous queries well
- Simple pipeline: query → retrieve → inject → respond
- No extra LLM round-trips for tool calls

**Limitations:**

- Context pollution: irrelevant chunks waste tokens
- Always-on cost: KB queried even when not needed
- No structured queries (filters, pagination, date ranges)
- Less transparent — users don't see KB was consulted

**Best for:** Scenarios where KB context must always be available; conversational assistants; smaller knowledge bases.

---

## Tool Calling — LLM-Driven Retrieval

The AI has access to KB search endpoints (e.g., `/knowledge/search`). It decides when to query the KB based on the user's question.

**Strengths:**

- Transparent: users can see when and what is searched
- Supports structured queries (filters, date ranges, pagination)
- Cost-efficient: only queries KB when needed
- Clear audit trail; fine-grained access control per endpoint

**Limitations:**

- Risk of missed tool calls (AI may not recognize when KB is needed)
- AI might answer from training data when it should check the KB
- Additional latency per tool call round-trip

**Best for:** Auditable applications; large KBs; teams prioritizing transparency and structured access.

---

## Hybrid — Best of Both Worlds

Combines RAG (automatic context injection) with Tool Calling (explicit structured queries). The RAG layer provides a safety net; tool calls handle complex or structured retrieval.

**Routing logic:**

```
Incoming message
├── RAG: semantic search → inject top-K chunks into context
└── Tool Calling: AI can additionally call /knowledge/search with filters
```

**Why Hybrid wins:**

- RAG ensures relevant context is always available
- Tool calls enable structured, filtered, and paginated access
- Balances reliability with transparency and cost

**Best for:** Production assistants; complex knowledge domains; applications needing both reliability and auditability.

---

## Side-by-Side Comparison

| Aspect                 | RAG                   | Tool Calling         | Hybrid                  |
| ---------------------- | --------------------- | -------------------- | ----------------------- |
| **Reliability**        | ✅ Always retrieves   | ⚠️ AI-dependent      | ✅ RAG safety net       |
| **Transparency**       | ❌ Hidden             | ✅ Visible           | ✅ Visible tool calls   |
| **Structured Queries** | ❌ Semantic only      | ✅ Full filters      | ✅ Both                 |
| **Token Cost**         | ⚠️ Always uses tokens | ✅ On demand         | ⚠️ RAG baseline + tools |
| **Complexity**         | ✅ Simple             | ⚠️ Prompt tuning     | ⚠️ Most complex         |
| **Latency**            | ✅ Single LLM call    | ⚠️ Extra round-trips | ⚠️ RAG + optional tools |

---

## Our Implementation

The plugin uses the **Hybrid approach** by default when a `VectorDB` is provided:

1. On every WebSocket message, the `WebSocketHandler` performs a semantic search against the vector DB
2. Top-K results are injected into the system prompt
3. Additionally, if `allowed_paths` includes KB endpoints, the AI can call them directly

See [RAG Feature](rag-feature.md) for setup instructions.
