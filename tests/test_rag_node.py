import types

import pytest

# Imported for real before the stubs below are installed (it needs the real
# langchain_core), then handed to rag.py as-is.
import autolangchat.prompt_caching as _prompt_caching

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
        "autolangchat.prompt_caching": _prompt_caching,
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
                "similarity_score": 0.68,
                "semantic_component": 0.8,
                "keyword_component": 0.4,
                "content": "Relevant context",
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "chunk_index": 0,
            }
        ]


@pytest.mark.asyncio
async def test_rag_node_preserves_user_message_when_injecting_context(caplog):
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

    with caplog.at_level("DEBUG", logger="autolangchat.graph.nodes.rag"):
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
    assert "score=0.6800 (semantic=0.8000 x 0.70=0.5600, keyword=0.4000 x 0.30=0.1200) [chunk-1]" in caplog.text


class _CachingChatConfig(_DummyChatConfig):
    model_id = "global.anthropic.claude-sonnet-5"
    prompt_caching_enabled = True


def _rag_config(chat_config, auth_context_text="AUTH CONTEXT"):
    return {
        "configurable": {
            "chat_config": chat_config,
            "kb_store": _DummyKBStore(),
            "embedding_client": _DummyEmbeddingClient(),
            "auth_context_text": auth_context_text,
        }
    }


def _user_turn():
    return {"messages": [{"role": "user", "content": "question"}], "metadata": {}}


@pytest.mark.asyncio
async def test_rag_node_puts_context_first_when_prompt_caching_is_off():
    """Legacy order (pre-XMGPLAT-11867) is kept byte-for-byte when caching is off."""
    result = await rag_node(_user_turn(), _rag_config(_DummyChatConfig()))

    system = result["messages"][0]
    assert system["content"].startswith("RELEVANT KNOWLEDGE BASE CONTEXT:")
    assert system["content"].endswith("\n\nAUTH CONTEXT\n\nBASE PROMPT")
    assert "metadata" not in system


@pytest.mark.asyncio
async def test_rag_node_puts_stable_prompt_first_when_prompt_caching_is_active():
    """XMGPLAT-11867: stable base prompt first, then KB context, then auth context."""
    result = await rag_node(_user_turn(), _rag_config(_CachingChatConfig()))

    system = result["messages"][0]
    content = system["content"]
    assert content.startswith("BASE PROMPT\n\nRELEVANT KNOWLEDGE BASE CONTEXT:")
    assert content.endswith("\n\nAUTH CONTEXT")
    assert content.index("Relevant context") < content.index("AUTH CONTEXT")
    assert system["metadata"] == {"cache_prefix_chars": len("BASE PROMPT")}
    assert content[: system["metadata"]["cache_prefix_chars"]] == "BASE PROMPT"
    assert [m["role"] for m in result["messages"]] == ["system", "user"]


@pytest.mark.asyncio
async def test_rag_node_marks_base_prompt_stable_without_context():
    chat_config = _CachingChatConfig()
    chat_config.enable_rag = False
    result = await rag_node(_user_turn(), _rag_config(chat_config, auth_context_text=None))

    system = result["messages"][0]
    assert system["content"] == "BASE PROMPT"
    assert system["metadata"] == {"cache_prefix_chars": len("BASE PROMPT")}


@pytest.mark.asyncio
async def test_rag_node_keeps_legacy_order_for_models_without_prompt_caching():
    chat_config = _CachingChatConfig()
    chat_config.model_id = "meta.llama3-3-70b-instruct-v1:0"
    result = await rag_node(_user_turn(), _rag_config(chat_config))

    assert result["messages"][0]["content"].endswith("BASE PROMPT")
    assert "metadata" not in result["messages"][0]
