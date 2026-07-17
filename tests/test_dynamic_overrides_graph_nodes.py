"""XMGPLAT-9697 Phase 3 — verify feature-toggle overrides propagate through
LangGraph nodes via `config["configurable"]["chat_config"]` (not a shared
`self.config` attribute).

Both `preprocess_node` and `rag_node` already read `chat_config` fresh from
`config["configurable"]` on every call, so passing the per-turn effective
config built in `websocket_handler.py` (Phase 2) makes these toggles "just
work" -- this file is verification, not new plumbing. Tests mock
`config["configurable"]["chat_config"]` directly per the plan, using a real
`ChatConfig` (`.model_copy(update=...)`) rather than a hand-rolled stub, since
`ChatConfig` fields only accept their pydantic alias at construction time
(see testing-env-notes.md).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autolangchat.config import ChatConfig
from autolangchat.graph.nodes.preprocess import preprocess_node
from autolangchat.graph.nodes.rag import rag_node


def _config(**overrides):
    return ChatConfig().model_copy(update=overrides)


# ---------------------------------------------------------------------------
# preprocess_node: enable_ai_summarization
# ---------------------------------------------------------------------------


class TestPreprocessNodeAiSummarizationToggle:
    @pytest.mark.asyncio
    async def test_enable_ai_summarization_true_builds_summarizer_llm(self):
        chat_config = _config(enable_ai_summarization=True)
        state = {"messages": [{"role": "user", "content": "hi"}], "metadata": {}}
        config = {"configurable": {"chat_config": chat_config}}

        with patch("autolangchat.graph.nodes.preprocess._build_llm") as mock_build_llm:
            mock_build_llm.return_value = MagicMock()
            await preprocess_node(state, config)

        mock_build_llm.assert_called_once()
        # The LLM is built from a *copy* of the effective chat_config (fixed
        # summarization temperature), not the global default.
        called_config = mock_build_llm.call_args.args[1]
        assert called_config.model_id == chat_config.model_id
        assert called_config is not chat_config

    @pytest.mark.asyncio
    async def test_enable_ai_summarization_false_skips_llm_build(self):
        chat_config = _config(enable_ai_summarization=False)
        state = {"messages": [{"role": "user", "content": "hi"}], "metadata": {}}
        config = {"configurable": {"chat_config": chat_config}}

        with patch("autolangchat.graph.nodes.preprocess._build_llm") as mock_build_llm:
            await preprocess_node(state, config)

        mock_build_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_chat_config_skips_preprocessing_gracefully(self):
        """No chat_config in configurable (e.g. a misconfigured caller) must not raise."""
        state = {"messages": [{"role": "user", "content": "hi"}], "metadata": {}}
        result = await preprocess_node(state, {"configurable": {}})
        assert result == {}


# ---------------------------------------------------------------------------
# rag_node: enable_rag, kb_top_k_results, kb_similarity_threshold
# ---------------------------------------------------------------------------


class _DummyEmbeddingClient:
    async def generate_embedding(self, text, model_id):
        return [0.1, 0.2, 0.3]


class _DummyKBStore:
    def __init__(self):
        self.hybrid_search = MagicMock(
            return_value=[
                {
                    "similarity_score": 0.9,
                    "content": "Relevant context",
                    "chunk_id": "chunk-1",
                    "document_id": "doc-1",
                    "chunk_index": 0,
                }
            ]
        )

    def close(self):
        pass


def _rag_state():
    return {"messages": [{"role": "user", "content": "What is the refund policy?"}]}


class TestRagNodeEnableRagToggle:
    @pytest.mark.asyncio
    async def test_enable_rag_true_runs_kb_search(self):
        chat_config = _config(enable_rag=True)
        kb_store = _DummyKBStore()
        config = {
            "configurable": {
                "chat_config": chat_config,
                "kb_store": kb_store,
                "embedding_client": _DummyEmbeddingClient(),
            }
        }

        result = await rag_node(_rag_state(), config)

        kb_store.hybrid_search.assert_called_once()
        assert result["kb_results"]

    @pytest.mark.asyncio
    async def test_enable_rag_false_skips_kb_search(self):
        chat_config = _config(enable_rag=False)
        kb_store = _DummyKBStore()
        config = {
            "configurable": {
                "chat_config": chat_config,
                "kb_store": kb_store,
                "embedding_client": _DummyEmbeddingClient(),
            }
        }

        result = await rag_node(_rag_state(), config)

        kb_store.hybrid_search.assert_not_called()
        assert result["kb_results"] == []


class TestRagNodeKbParamOverrides:
    @pytest.mark.asyncio
    async def test_kb_top_k_results_and_similarity_threshold_reach_hybrid_search(self):
        chat_config = _config(enable_rag=True, kb_top_k_results=7, kb_similarity_threshold=0.42)
        kb_store = _DummyKBStore()
        config = {
            "configurable": {
                "chat_config": chat_config,
                "kb_store": kb_store,
                "embedding_client": _DummyEmbeddingClient(),
            }
        }

        await rag_node(_rag_state(), config)

        _, call_kwargs = kb_store.hybrid_search.call_args
        assert call_kwargs["limit"] == 7
        assert call_kwargs["min_score"] == 0.42

    @pytest.mark.asyncio
    async def test_default_kb_params_used_when_not_overridden(self):
        chat_config = _config(enable_rag=True)
        kb_store = _DummyKBStore()
        config = {
            "configurable": {
                "chat_config": chat_config,
                "kb_store": kb_store,
                "embedding_client": _DummyEmbeddingClient(),
            }
        }

        await rag_node(_rag_state(), config)

        _, call_kwargs = kb_store.hybrid_search.call_args
        assert call_kwargs["limit"] == ChatConfig().kb_top_k_results
        assert call_kwargs["min_score"] == ChatConfig().kb_similarity_threshold
