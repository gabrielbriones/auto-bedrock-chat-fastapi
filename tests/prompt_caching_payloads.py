"""Capture the exact Bedrock ``Converse`` request built for a chat turn.

Shared by ``test_prompt_caching.py``: each scenario runs ``rag_node`` and
``llm_call_node`` for real, with botocore's ``_make_api_call`` patched so
nothing leaves the process, and returns the request parameters that would
have been sent. ``fixtures/prompt_caching/parity_payloads.json`` holds the
payloads captured before prompt caching existed; with caching off (or on a
model that doesn't support it) the payloads must still match it exactly.

Regenerate the snapshot only when an intended, caching-unrelated payload
change lands::

    poetry run python -m tests.prompt_caching_payloads
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

from langchain_core.tools import tool

from autolangchat.config import ChatConfig
from autolangchat.graph.nodes.llm_call import llm_call_node
from autolangchat.graph.nodes.rag import rag_node

SNAPSHOT_PATH = Path(__file__).parent / "fixtures" / "prompt_caching" / "parity_payloads.json"

CACHING_MODEL = "global.anthropic.claude-sonnet-5"
NON_CACHING_MODEL = "openai.gpt-oss-120b-1:0"

BASE_PROMPT = "You are the parity-test assistant. " + " ".join(
    f"Guideline {i}: answer questions about topic {i} precisely and cite sources." for i in range(200)
)
AUTH_CONTEXT = "AUTHENTICATED USER: alice@example.com (groups: engineering)"
TOOL_CALL_ID = "tooluse_parity_0001"


@tool
def get_weather(city: str) -> str:
    """Return the current weather for a city."""
    return f"Sunny in {city}"


class _EmbeddingClient:
    async def generate_embedding(self, text, model_id):
        return [0.1, 0.2, 0.3]


class _KBStore:
    def hybrid_search(self, **kwargs):
        return [
            {
                "similarity_score": 0.81,
                "semantic_component": 0.9,
                "keyword_component": 0.6,
                "title": "Weather API",
                "source": "docs",
                "source_url": "https://example.com/weather",
                "content": "The weather endpoint returns the forecast for a city.",
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "chunk_index": 0,
            }
        ]

    def close(self):
        pass


_CONVERSE_RESPONSE = {
    "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
    "stopReason": "end_turn",
    "usage": {"inputTokens": 10, "outputTokens": 2, "totalTokens": 12},
    "metrics": {"latencyMs": 1},
}


def chat_config(model_id: str, **overrides: Any) -> ChatConfig:
    """Deterministic config for payload scenarios (no .env-dependent values)."""
    values: Dict[str, Any] = {
        "model_id": model_id,
        "system_prompt": BASE_PROMPT,
        "max_tokens": 1024,
        "temperature": 0.2,
        "top_p": None,
        "enable_rag": True,
        "kb_embedding_model": "test-embedding-model",
        "kb_top_k_results": 3,
        "kb_similarity_threshold": 0.5,
        "kb_semantic_weight": 0.7,
        "kb_keyword_weight": 0.3,
        "langchain_tools": [get_weather],
        "fallback_model": None,
        "aws_region": "us-west-2",
        "aws_access_key_id": None,
        "aws_secret_access_key": None,
    }
    values.update(overrides)
    return ChatConfig().model_copy(update=values)


def _first_turn() -> List[Dict[str, Any]]:
    return [{"role": "user", "content": "What's the weather in Paris?"}]


def _tool_round_two() -> List[Dict[str, Any]]:
    return [
        {"role": "user", "content": "What's the weather in Paris?"},
        {
            "role": "assistant",
            "content": "Let me check.",
            "tool_calls": [{"name": "get_weather", "args": {"city": "Paris"}, "id": TOOL_CALL_ID, "type": "tool_call"}],
        },
        {
            "role": "tool",
            "content": "",
            "tool_results": [{"tool_call_id": TOOL_CALL_ID, "name": "get_weather", "result": "Sunny in Paris"}],
        },
    ]


def _follow_up_turn() -> List[Dict[str, Any]]:
    return _tool_round_two() + [
        {"role": "assistant", "content": "It is sunny in Paris.", "tool_calls": []},
        {"role": "user", "content": "And in Rome?"},
    ]


# name -> (message builder, use KB retrieval, auth context, run rag_node)
SCENARIOS: Dict[str, tuple] = {
    "plain_first_turn": (_first_turn, False, None, True),
    "rag_and_auth_first_turn": (_first_turn, True, AUTH_CONTEXT, True),
    "rag_follow_up_turn": (_follow_up_turn, True, AUTH_CONTEXT, True),
    "tool_round_two": (_tool_round_two, False, None, False),
    "rag_tool_round_two": (_tool_round_two, True, AUTH_CONTEXT, False),
}


async def capture_payload(scenario: str, config: ChatConfig, *, on_progress: Any = None) -> Dict[str, Any]:
    """Run ``scenario`` through ``rag_node`` + ``llm_call_node`` and return the Converse params."""
    build_messages, use_kb, auth_context, run_rag_as_user_turn = SCENARIOS[scenario]
    messages = build_messages()
    configurable: Dict[str, Any] = {
        "chat_config": config,
        "kb_store": _KBStore() if use_kb else None,
        "embedding_client": _EmbeddingClient() if use_kb else None,
        "auth_context_text": auth_context,
    }
    if not use_kb:
        configurable["chat_config"] = config.model_copy(update={"enable_rag": False})
    runnable_config = {"configurable": configurable}

    if run_rag_as_user_turn:
        rag_update = await rag_node({"messages": messages, "metadata": {}}, runnable_config)
        messages = rag_update.get("messages", messages)
    else:
        # Tool rounds don't re-run retrieval: build the system message from the
        # user turn that started the loop, then append the tool round.
        rag_update = await rag_node({"messages": messages[:1], "metadata": {}}, runnable_config)
        messages = rag_update.get("messages", messages[:1])[:-1] + messages

    captured: List[Dict[str, Any]] = []

    def _fake_api_call(_client, operation_name, api_params):
        captured.append({"operation": operation_name, "params": api_params})
        return json.loads(json.dumps(_CONVERSE_RESPONSE))

    with patch("botocore.client.BaseClient._make_api_call", _fake_api_call):
        await llm_call_node(
            {"messages": messages, "metadata": {}},
            {"configurable": {"chat_config": configurable["chat_config"], "on_progress": on_progress}},
        )

    assert len(captured) == 1, captured
    return json.loads(json.dumps(captured[0]["params"], default=str))


async def capture_all() -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {}
    for model_id in (CACHING_MODEL, NON_CACHING_MODEL):
        for scenario in SCENARIOS:
            snapshot[f"{model_id}::{scenario}"] = await capture_payload(scenario, chat_config(model_id))
    return snapshot


if __name__ == "__main__":
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(asyncio.run(capture_all()), indent=2, sort_keys=True) + "\n")
    print(f"Wrote {SNAPSHOT_PATH}")
