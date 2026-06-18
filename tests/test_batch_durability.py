"""Phase 4 — batch durability tests.

Verifies per-job checkpointing and retry behaviour using MemorySaver (no
Postgres required).

Test scenarios
--------------
1. Full happy-path: all 5 jobs complete → interrupt fires → accepted → report.
2. Job failure: job 3 raises → graph captures failed_jobs=[j3].
3. Durability: fail job 3 mid-batch, verify completed_jobs=[j1,j2] in state.
4. Retry resumes: second ainvoke with same thread_id skips j1/j2, runs j3–j5.
5. Cancellation: user declines confirmation → cancelled=True, no report.
6. Empty job_ids: route_jobs short-circuits directly to aggregate.
7. route_jobs skips already-completed jobs (idempotency guard).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autolangchat.graph.batch_graph import (
    BatchState,
    aggregate_results_node,
    build_batch_graph,
    cancel_batch_node,
    process_job_node,
    route_jobs,
    should_generate_report,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeChatConfig:
    model_id = "test-model"
    fallback_model = None
    aws_region = "us-east-1"
    temperature = 0.7
    max_tokens = 1024
    top_p = 0.9
    checkpoint_postgres_url = None
    checkpoint_pool_size = 5


def _empty_state(**overrides) -> BatchState:
    base: BatchState = {
        "job_ids": [],
        "completed_jobs": [],
        "job_results": {},
        "failed_jobs": [],
        "report": None,
        "cancelled": False,
        "metadata": {},
    }
    base.update(overrides)
    return base


def _make_graph(job_processor=None):
    """Build the batch graph with a mock LLM for generate_report."""
    graph = build_batch_graph(_FakeChatConfig())
    return graph


# ---------------------------------------------------------------------------
# Unit: route_jobs
# ---------------------------------------------------------------------------


class TestRouteJobs:
    def test_dispatches_all_jobs_when_none_completed(self):
        from langgraph.types import Send

        state = _empty_state(job_ids=["j1", "j2", "j3"])
        sends = route_jobs(state)
        assert len(sends) == 3
        job_ids_dispatched = {s.node == "process_job" and s.arg["job_id"] for s in sends}
        for s in sends:
            assert s.node == "process_job"

    def test_skips_already_completed_jobs(self):
        state = _empty_state(job_ids=["j1", "j2", "j3"], completed_jobs=["j1", "j2"])
        sends = route_jobs(state)
        assert len(sends) == 1
        assert sends[0].arg["job_id"] == "j3"

    def test_short_circuits_when_all_completed(self):
        state = _empty_state(job_ids=["j1", "j2"], completed_jobs=["j1", "j2"])
        sends = route_jobs(state)
        # All done → single Send to aggregate_results
        assert len(sends) == 1
        assert sends[0].node == "aggregate_results"

    def test_short_circuits_when_no_job_ids(self):
        state = _empty_state(job_ids=[])
        sends = route_jobs(state)
        assert len(sends) == 1
        assert sends[0].node == "aggregate_results"


# ---------------------------------------------------------------------------
# Unit: process_job_node
# ---------------------------------------------------------------------------


class TestProcessJobNode:
    @pytest.mark.asyncio
    async def test_calls_job_processor_and_returns_result(self):
        async def _proc(job_id, config=None):
            return {"output": f"data-{job_id}"}

        state = {**_empty_state(job_ids=["j1"]), "job_id": "j1"}
        cfg = {"configurable": {"job_processor": _proc}}
        result = await process_job_node(state, cfg)
        assert result["completed_jobs"] == ["j1"]
        assert result["job_results"] == {"j1": {"output": "data-j1"}}
        assert result["failed_jobs"] == []

    @pytest.mark.asyncio
    async def test_records_failure_without_raising(self):
        async def _failing(job_id, config=None):
            raise RuntimeError("downstream error")

        state = {**_empty_state(job_ids=["j2"]), "job_id": "j2"}
        cfg = {"configurable": {"job_processor": _failing}}
        result = await process_job_node(state, cfg)
        assert result["completed_jobs"] == []
        assert result["failed_jobs"] == ["j2"]
        assert result["job_results"] == {}

    @pytest.mark.asyncio
    async def test_no_processor_stub_returns_ok(self):
        state = {**_empty_state(job_ids=["j3"]), "job_id": "j3"}
        result = await process_job_node(state, {})
        assert result["completed_jobs"] == ["j3"]
        assert "j3" in result["job_results"]


# ---------------------------------------------------------------------------
# Unit: should_generate_report
# ---------------------------------------------------------------------------


class TestShouldGenerateReport:
    def test_routes_to_generate_report_when_not_cancelled(self):
        assert should_generate_report(_empty_state(cancelled=False)) == "generate_report"

    def test_routes_to_cancel_batch_when_cancelled(self):
        assert should_generate_report(_empty_state(cancelled=True)) == "cancel_batch"


# ---------------------------------------------------------------------------
# Integration: full graph with MemorySaver
# ---------------------------------------------------------------------------


class TestBatchGraphIntegration:
    """Full graph runs using MemorySaver — no Postgres needed."""

    @pytest.mark.asyncio
    async def test_happy_path_all_jobs_complete(self):
        """5 jobs → all succeed → interrupt → accept → report generated."""
        from langgraph.types import Command

        graph = _make_graph()
        tid = f"batch-{uuid.uuid4().hex[:8]}"
        cfg = {"configurable": {"thread_id": tid, "chat_config": _FakeChatConfig()}}

        initial = _empty_state(job_ids=["j1", "j2", "j3", "j4", "j5"])
        state1 = await graph.ainvoke(initial, config=cfg)

        # Graph should be paused at HITL interrupt
        assert "__interrupt__" in state1, "Expected HITL interrupt after all jobs"
        assert set(state1["completed_jobs"]) == {"j1", "j2", "j3", "j4", "j5"}
        assert state1["failed_jobs"] == []

        # Resume with proceed=True
        mock_llm = MagicMock()
        ai_msg = MagicMock()
        ai_msg.content = "Generated report text"
        mock_llm.ainvoke = AsyncMock(return_value=ai_msg)
        with patch("autolangchat.graph.batch_graph.ChatBedrockConverse", return_value=mock_llm):
            state2 = await graph.ainvoke(Command(resume={"proceed": True}), config=cfg)
        assert state2.get("report") == "Generated report text"
        assert not state2.get("cancelled", False)

    @pytest.mark.asyncio
    async def test_job_failure_captured_in_failed_jobs(self):
        """Job 3 raises → failed_jobs=[j3], graph still proceeds to interrupt."""
        from langgraph.types import Command

        graph = _make_graph()
        tid = f"batch-{uuid.uuid4().hex[:8]}"
        cfg = {"configurable": {"thread_id": tid, "chat_config": _FakeChatConfig()}}

        call_count = {"n": 0}

        async def _flaky_processor(job_id, config=None):
            call_count["n"] += 1
            if job_id == "j3":
                raise ValueError("job 3 failed")
            return {"data": job_id}

        cfg["configurable"]["job_processor"] = _flaky_processor

        initial = _empty_state(job_ids=["j1", "j2", "j3"])
        state1 = await graph.ainvoke(initial, config=cfg)

        assert "__interrupt__" in state1
        assert "j3" in state1["failed_jobs"]
        assert "j3" not in state1["completed_jobs"]
        assert {"j1", "j2"} <= set(state1["completed_jobs"])

    @pytest.mark.asyncio
    async def test_durability_retry_skips_completed_jobs(self):
        """Simulate failure mid-batch: manually build state with completed_jobs=[j1,j2],
        then run a 'retry' (new ainvoke on same thread) and verify only j3–j5 run."""
        from langgraph.types import Command

        graph = _make_graph()
        tid = f"batch-retry-{uuid.uuid4().hex[:8]}"
        cfg = {"configurable": {"thread_id": tid, "chat_config": _FakeChatConfig()}}

        processed_jobs: List[str] = []

        async def _tracking_processor(job_id, config=None):
            processed_jobs.append(job_id)
            return {"data": job_id}

        cfg["configurable"]["job_processor"] = _tracking_processor

        # First run: provide all 5 jobs but completed_jobs=[j1,j2] (simulating prior partial run)
        initial = _empty_state(
            job_ids=["j1", "j2", "j3", "j4", "j5"],
            completed_jobs=["j1", "j2"],
            job_results={"j1": {"data": "j1"}, "j2": {"data": "j2"}},
        )
        state1 = await graph.ainvoke(initial, config=cfg)

        # Only j3,j4,j5 should have been processed
        assert "j1" not in processed_jobs
        assert "j2" not in processed_jobs
        assert {"j3", "j4", "j5"} <= set(processed_jobs)
        assert "__interrupt__" in state1

        # Accept and get report
        mock_llm = MagicMock()
        ai_msg = MagicMock()
        ai_msg.content = "retry report"
        mock_llm.ainvoke = AsyncMock(return_value=ai_msg)
        with patch("autolangchat.graph.batch_graph.ChatBedrockConverse", return_value=mock_llm):
            state2 = await graph.ainvoke(Command(resume={"proceed": True}), config=cfg)
        assert state2.get("report") is not None

    @pytest.mark.asyncio
    async def test_cancellation_sets_cancelled_flag(self):
        """User declines confirmation → cancelled=True, no report."""
        from langgraph.types import Command

        graph = _make_graph()
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock()
        tid = f"batch-cancel-{uuid.uuid4().hex[:8]}"
        cfg = {"configurable": {"thread_id": tid, "chat_config": _FakeChatConfig()}}

        state1 = await graph.ainvoke(_empty_state(job_ids=["j1"]), config=cfg)
        assert "__interrupt__" in state1

        with patch("autolangchat.graph.batch_graph.ChatBedrockConverse", return_value=mock_llm):
            state2 = await graph.ainvoke(Command(resume={"proceed": False}), config=cfg)
        assert state2.get("cancelled") is True
        assert state2.get("report") is None
        mock_llm.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_job_ids_goes_straight_to_interrupt(self):
        """When job_ids=[], route_jobs short-circuits and interrupt fires immediately."""
        from langgraph.types import Command

        graph = _make_graph()
        tid = f"batch-empty-{uuid.uuid4().hex[:8]}"
        cfg = {"configurable": {"thread_id": tid, "chat_config": _FakeChatConfig()}}

        state1 = await graph.ainvoke(_empty_state(job_ids=[]), config=cfg)
        assert "__interrupt__" in state1
        assert state1["completed_jobs"] == []
