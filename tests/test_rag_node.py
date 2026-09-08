import types

import pytest

from ._autolangchat_imports import load_module

_graph_pkg = types.ModuleType("autolangchat.graph")
_graph_pkg.__path__ = []
_nodes_pkg = types.ModuleType("autolangchat.graph.nodes")
_nodes_pkg.__path__ = []
_state_mod = types.ModuleType("autolangchat.graph.state")
_state_mod.ChatState = dict

_langchain_core_pkg = types.ModuleType("langchain_core")
_langchain_core_pkg.__path__ = []
_runnables_mod = types.ModuleType("langchain_core.runnables")
_runnables_mod.RunnableConfig = dict

rag_node = load_module(
    "autolangchat.graph.nodes.rag",
    "graph/nodes/rag.py",
    extra_modules={
        "autolangchat.graph": _graph_pkg,
        "autolangchat.graph.nodes": _nodes_pkg,
        "autolangchat.graph.state": _state_mod,
        "langchain_core": _langchain_core_pkg,
        "langchain_core.runnables": _runnables_mod,
    },
).rag_node


class _DummyChatConfig:
    enable_rag = True
    kb_embedding_model = "test-embedding-model"
    kb_top_k_results = 5
    kb_similarity_threshold = 0.5
    kb_semantic_weight = 0.7
    kb_keyword_weight = 0.3

    def get_system_prompt(self):
        return "BASE PROMPT"


class _DummyEmbeddingClient:
    async def generate_embedding(self, text, model_id):
        return [0.1, 0.2, 0.3]


class _DummyKBStore:
    def hybrid_search(self, **kwargs):
        return [
            {
                "similarity_score": 0.9,
                "content": "Relevant context",
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "chunk_index": 0,
            }
        ]


@pytest.mark.asyncio
async def test_rag_node_preserves_user_message_when_injecting_context():
    state = {
        "messages": [
            {"role": "system", "content": "old system prompt"},
            {"role": "user", "content": "If I return additional status codes, will they be included?"},
        ],
        "metadata": {},
    }
    config = {
        "configurable": {
            "chat_config": _DummyChatConfig(),
            "kb_store": _DummyKBStore(),
            "embedding_client": _DummyEmbeddingClient(),
            "auth_context_text": "AUTH CONTEXT",
        }
    }

    result = await rag_node(state, config)

    roles = [message["role"] for message in result["messages"]]
    assert roles.count("system") == 1
    assert roles[-1] == "user"
    assert any(
        message["role"] == "user"
        and message["content"] == "If I return additional status codes, will they be included?"
        for message in result["messages"]
    )
    assert result["kb_results"]
