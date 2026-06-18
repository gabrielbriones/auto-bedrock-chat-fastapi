"""Batch analysis graph — Phase 4.

Processes a list of job IDs in parallel, then prompts the user for
confirmation before generating a final report.

Graph topology
--------------

    START
      ↓
    [route_jobs] ── Send("process_job") ×N (fan-out)
                          ↓
                    [process_job]  (one per job_id, parallel)
                          ↓
                    [aggregate_results]  (fan-in — waits for all Send branches)
                          ↓
                    [request_confirmation]  ← interrupt() — pauses here
                          ↓           (resume via Command(resume={"proceed": T/F}))
                    [should_generate_report]
                    /               \\
          [generate_report]       [cancel_batch]
                ↓                       ↓
               END                     END

Checkpointing
-------------
Every node completion is checkpointed automatically (Postgres or MemorySaver).
On failure mid-batch:
  - ``completed_jobs`` lists the already-processed job IDs.
  - Retry by calling ``graph.ainvoke(None, config={"configurable": {"thread_id": batch_id}})``.
    The graph resumes from the last checkpoint and ``route_jobs`` skips
    already-completed jobs.

HITL flow
---------
1. ``ainvoke()`` returns with ``state["__interrupt__"]`` set.
2. Caller reads the interrupt payload and sends ``confirmation_required``
   to the WebSocket client.
3. Client responds ``{"type": "confirm", "batch_id": "...", "proceed": true}``.
4. Caller resumes: ``graph.ainvoke(Command(resume={"proceed": True}), config=…)``.
"""

from __future__ import annotations

import asyncio
import logging
import operator
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from langchain_aws import ChatBedrockConverse
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt
from typing_extensions import Annotated, TypedDict

from .checkpointer import build_checkpointer

if TYPE_CHECKING:
    from ..config import ChatConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def _merge_dicts(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    return {**a, **b}


class BatchState(TypedDict):
    """Mutable state for one batch run.

    Annotated fields use *reducer* functions so parallel ``Send`` branches
    can safely merge their results into the shared state without clobbering
    each other.
    """

    # Inputs (set once at the start)
    job_ids: List[str]
    """All job IDs to process in this batch."""

    # Accumulated across parallel Send branches
    completed_jobs: Annotated[List[str], operator.add]
    """Job IDs that have been successfully processed."""

    job_results: Annotated[Dict[str, Any], _merge_dicts]
    """Keyed by job_id.  Each value is the dict returned by the job processor."""

    failed_jobs: Annotated[List[str], operator.add]
    """Job IDs that failed processing."""

    # Written by single nodes
    report: Optional[str]
    """Final generated report text (set by generate_report node)."""

    cancelled: bool
    """True when the user declined the confirmation prompt."""

    metadata: Dict[str, Any]
    """Pass-through for on_progress callback, auth_info, etc."""


class _ProcessJobInput(TypedDict):
    """Minimal state dict passed to each process_job Send branch."""

    job_ids: List[str]
    completed_jobs: List[str]
    job_results: Dict[str, Any]
    failed_jobs: List[str]
    report: Optional[str]
    cancelled: bool
    metadata: Dict[str, Any]
    # Per-branch extra field
    job_id: str


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def route_jobs(state: BatchState) -> List[Send]:
    """Fan-out: dispatch one Send per un-completed job.

    On retry (resume from checkpoint), ``completed_jobs`` is already
    populated, so only the remaining jobs are dispatched.
    """
    remaining = [j for j in state["job_ids"] if j not in state["completed_jobs"]]
    if not remaining:
        logger.info("route_jobs: all jobs already completed — skipping to aggregate")
        # Return a single no-op Send that resolves to the aggregate node
        # by short-circuiting: route directly to aggregate with a sentinel
        return [Send("aggregate_results", state)]
    logger.info("route_jobs: dispatching %d job(s): %s", len(remaining), remaining)
    return [Send("process_job", {"job_id": j, **state}) for j in remaining]


async def process_job_node(state: _ProcessJobInput, config: RunnableConfig) -> Dict:
    """Process a single job.

    Delegates to an optional ``job_processor`` callable injected via
    ``config["configurable"]["job_processor"]``.  Falls back to a no-op
    that returns an empty result — callers should always supply a real
    processor.

    Returns reducer-compatible updates.
    """
    job_id = state.get("job_id", "unknown")
    configurable = (config or {}).get("configurable", {})
    job_processor: Optional[Callable] = configurable.get("job_processor")
    on_progress: Optional[Callable] = configurable.get("on_progress")

    logger.info("process_job: starting job_id=%s", job_id)
    try:
        if job_processor is not None:
            result = await job_processor(job_id, config=configurable)
        else:
            # No-op stub — real applications must supply job_processor
            result = {"status": "ok", "data": None}

        if on_progress:
            await on_progress({"type": "job_progress", "job_id": job_id, "status": "completed"})

        logger.info("process_job: completed job_id=%s", job_id)
        return {
            "completed_jobs": [job_id],
            "job_results": {job_id: result},
            "failed_jobs": [],
        }
    except Exception as exc:
        logger.error("process_job: failed job_id=%s: %s", job_id, exc)
        if on_progress:
            await on_progress({"type": "job_progress", "job_id": job_id, "status": "failed", "error": str(exc)})
        return {
            "completed_jobs": [],
            "job_results": {},
            "failed_jobs": [job_id],
        }


def aggregate_results_node(state: BatchState, config: RunnableConfig) -> Dict:
    """Fan-in: summarise after all process_job branches complete."""
    n_ok = len(state.get("completed_jobs", []))
    n_fail = len(state.get("failed_jobs", []))
    logger.info("aggregate_results: %d completed, %d failed", n_ok, n_fail)
    return {}


async def request_confirmation_node(state: BatchState, config: RunnableConfig) -> Dict:
    """HITL gate: pause execution and ask the user whether to generate the report.

    ``interrupt()`` suspends the graph here.  The caller (WebSocket handler)
    reads ``state["__interrupt__"]``, sends ``confirmation_required`` to the
    client, then resumes via ``Command(resume={"proceed": True/False})``.
    """
    n_ok = len(state.get("completed_jobs", []))
    n_fail = len(state.get("failed_jobs", []))

    proceed = interrupt(
        {
            "type": "confirmation_required",
            "message": (
                f"Processed {n_ok} job(s) successfully"
                + (f" ({n_fail} failed)" if n_fail else "")
                + ". Generate the final report?"
            ),
            "completed_count": n_ok,
            "failed_count": n_fail,
        }
    )

    # proceed is whatever the caller passed to Command(resume=...)
    confirmed = False
    if isinstance(proceed, dict):
        confirmed = bool(proceed.get("proceed", False))
    elif isinstance(proceed, bool):
        confirmed = proceed

    return {"cancelled": not confirmed}


async def generate_report_node(state: BatchState, config: RunnableConfig) -> Dict:
    """Call the LLM to generate the final analysis report."""
    configurable = (config or {}).get("configurable", {})
    chat_config = configurable.get("chat_config")
    on_progress: Optional[Callable] = configurable.get("on_progress")

    if on_progress:
        await on_progress({"type": "typing", "message": "Generating report…"})

    results_text = "\n".join(f"- Job {jid}: {res}" for jid, res in state.get("job_results", {}).items())

    model_id = (
        getattr(chat_config, "model_id", "us.anthropic.claude-sonnet-4-6")
        if chat_config
        else "us.anthropic.claude-sonnet-4-6"
    )
    aws_region = getattr(chat_config, "aws_region", "us-east-1") if chat_config else "us-east-1"
    temperature = getattr(chat_config, "temperature", 0.7) if chat_config else 0.7
    max_tokens = getattr(chat_config, "max_tokens", 4096) if chat_config else 4096
    aws_access_key_id = getattr(chat_config, "aws_access_key_id", None) if chat_config else None
    aws_secret_access_key = getattr(chat_config, "aws_secret_access_key", None) if chat_config else None

    llm_kwargs: Dict[str, Any] = {
        "model": model_id,
        "region_name": aws_region,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if aws_access_key_id and aws_secret_access_key:
        llm_kwargs["aws_access_key_id"] = aws_access_key_id
        llm_kwargs["aws_secret_access_key"] = aws_secret_access_key
    llm = ChatBedrockConverse(**llm_kwargs)

    messages = [
        SystemMessage(
            content=(
                "You are a technical analyst. "
                "Given the following job results, produce a concise analysis report. "
                "Be specific and actionable."
            )
        ),
        HumanMessage(content=f"Job results:\n{results_text}\n\nWrite the analysis report."),
    ]

    try:
        response = await llm.ainvoke(messages)
        report_text = response.content
        logger.info("generate_report: report generated (%d chars)", len(report_text))
    except Exception as exc:
        logger.error("generate_report: LLM call failed: %s", exc)
        report_text = f"Report generation failed: {exc}"

    if on_progress:
        await on_progress(
            {
                "type": "batch_complete",
                "message": report_text,
            }
        )

    return {"report": report_text}


def cancel_batch_node(state: BatchState, config: RunnableConfig) -> Dict:
    """Terminal node when the user declined confirmation."""
    configurable = (config or {}).get("configurable", {})
    on_progress: Optional[Callable] = configurable.get("on_progress")

    async def _notify():
        if on_progress:
            await on_progress({"type": "batch_cancelled", "message": "Batch report cancelled by user."})

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_notify())
    except RuntimeError:
        pass

    logger.info("cancel_batch: batch cancelled by user")
    return {}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def should_generate_report(state: BatchState) -> str:
    """Route to generate_report or cancel_batch based on user confirmation."""
    if state.get("cancelled", False):
        return "cancel_batch"
    return "generate_report"


# ---------------------------------------------------------------------------
# Graph factory
# ---------------------------------------------------------------------------


def build_batch_graph(config: "ChatConfig"):
    """Build and compile the batch analysis StateGraph.

    Parameters
    ----------
    config:
        Application ``ChatConfig``.  Used to read ``checkpoint_postgres_url``
        and ``checkpoint_pool_size`` so the batch graph shares the same
        checkpointer backend as the chat graph.

    Returns
    -------
    CompiledGraph
        A compiled LangGraph graph.  Call ``await graph.ainvoke(...)`` with a
        ``BatchState``-compatible dict and a ``thread_id`` in ``configurable``.
    """
    builder = StateGraph(BatchState)

    builder.add_node("process_job", process_job_node)
    builder.add_node("aggregate_results", aggregate_results_node)
    builder.add_node("request_confirmation", request_confirmation_node)
    builder.add_node("generate_report", generate_report_node)
    builder.add_node("cancel_batch", cancel_batch_node)

    # START fans out to one process_job Send per remaining job
    builder.add_conditional_edges(START, route_jobs, ["process_job", "aggregate_results"])
    # All process_job branches converge at aggregate_results
    builder.add_edge("process_job", "aggregate_results")
    # After aggregation, pause for HITL confirmation
    builder.add_edge("aggregate_results", "request_confirmation")
    # After confirmation (or cancellation), route to report or cancel
    builder.add_conditional_edges(
        "request_confirmation",
        should_generate_report,
        {"generate_report": "generate_report", "cancel_batch": "cancel_batch"},
    )
    builder.add_edge("generate_report", END)
    builder.add_edge("cancel_batch", END)

    # Build a dedicated checkpointer (separate pool from chat graph)
    postgres_url = getattr(config, "checkpoint_postgres_url", None)
    pool_size = getattr(config, "checkpoint_pool_size", 5)
    checkpointer = build_checkpointer(postgres_url=postgres_url, pool_size=pool_size)

    graph = builder.compile(checkpointer=checkpointer)

    logger.info(
        "LangGraph batch graph compiled (checkpointer: %s)",
        type(checkpointer).__name__,
    )
    return graph
