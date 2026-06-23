import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


def _load_rag_module():
    package_root = Path(__file__).resolve().parents[1] / "autolangchat"
    module_path = package_root / "graph" / "nodes" / "rag.py"

    autolangchat_pkg = types.ModuleType("autolangchat")
    autolangchat_pkg.__path__ = [str(package_root)]

    graph_pkg = types.ModuleType("autolangchat.graph")
    graph_pkg.__path__ = [str(package_root / "graph")]

    nodes_pkg = types.ModuleType("autolangchat.graph.nodes")
    nodes_pkg.__path__ = [str(package_root / "graph" / "nodes")]

    state_mod = types.ModuleType("autolangchat.graph.state")
    state_mod.ChatState = dict

    langchain_core_pkg = types.ModuleType("langchain_core")
    langchain_core_pkg.__path__ = []
    runnables_mod = types.ModuleType("langchain_core.runnables")
    runnables_mod.RunnableConfig = dict

    original_modules = {
        name: sys.modules.get(name)
        for name in [
            "autolangchat",
            "autolangchat.graph",
            "autolangchat.graph.nodes",
            "autolangchat.graph.state",
            "langchain_core",
            "langchain_core.runnables",
        ]
    }

    sys.modules["autolangchat"] = autolangchat_pkg
    sys.modules["autolangchat.graph"] = graph_pkg
    sys.modules["autolangchat.graph.nodes"] = nodes_pkg
    sys.modules["autolangchat.graph.state"] = state_mod
    sys.modules["langchain_core"] = langchain_core_pkg
    sys.modules["langchain_core.runnables"] = runnables_mod

    try:
        spec = spec_from_file_location("autolangchat.graph.nodes.rag", module_path)
        module = module_from_spec(spec)
        assert spec and spec.loader
        sys.modules["autolangchat.graph.nodes.rag"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


rag_node = _load_rag_module().rag_node


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
