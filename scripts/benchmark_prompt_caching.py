#!/usr/bin/env python3
"""Benchmark Bedrock prompt caching (XMGPLAT-11867).

Runs the chat graph's own ``rag_node`` + ``llm_call_node`` against real
Bedrock through a two-round tool loop (the model calls ``get_weather``, the
tool result is fed back, the model answers), first cold and then warm, and
reports per call:

* latency,
* input tokens (total, including cached), cache-read and cache-write tokens,
* cache hit rate (cache-read / input tokens),
* estimated cost, using the per-million-token prices given on the command
  line (defaults are Claude Sonnet-class list prices -- check
  https://aws.amazon.com/bedrock/pricing/ for your model and region).

Each invocation uses a fresh nonce in the system prompt, so the first run is
always a cold cache. With ``--compare`` the same runs are repeated with
caching off as a baseline.

Requires AWS credentials with ``bedrock:InvokeModel`` on the model, e.g.::

    poetry run python scripts/benchmark_prompt_caching.py
    poetry run python scripts/benchmark_prompt_caching.py --model us.anthropic.claude-haiku-4-5-20251001-v1:0 \\
        --warm-runs 5 --ttl 1h --compare --json results.json

Acceptance checks printed at the end: round 2 of a warm tool loop reports
``cache_read_tokens > 0``, and the system block is read from cache on a
retrieval turn (KB context changes, stable prompt doesn't).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.tools import tool  # noqa: E402

from autolangchat.config import ChatConfig  # noqa: E402
from autolangchat.graph.nodes.llm_call import llm_call_node  # noqa: E402
from autolangchat.graph.nodes.rag import rag_node  # noqa: E402
from autolangchat.model_capabilities import get_prompt_cache_min_tokens, supports_prompt_caching  # noqa: E402


@tool
def get_weather(city: str) -> str:
    """Return the current weather for a city."""
    return f"Sunny and 21C in {city}"


class _EmbeddingClient:
    async def generate_embedding(self, text, model_id):
        return [0.0]


class _KBStore:
    """Returns a different chunk per query, like real retrieval would."""

    def hybrid_search(self, query, **kwargs):
        return [
            {
                "similarity_score": 0.8,
                "title": "Weather guide",
                "source_url": "https://example.com/weather",
                "content": f"Retrieved for {query!r}: forecasts come from the get_weather tool.",
                "chunk_id": str(uuid.uuid4()),
            }
        ]

    def close(self):
        pass


def _stable_prompt(min_tokens: int) -> str:
    # Well above the model's minimum (~4 chars/token) so the system block is cacheable.
    lines = [f"Benchmark run {uuid.uuid4().hex}. You are a concise assistant that uses tools when needed."]
    lines += [
        f"Policy {i}: keep answers short, cite the tool you used, and never invent data."
        for i in range(max(min_tokens, 1024) // 12 + 50)
    ]
    return "\n".join(lines)


def _cost(usage: Dict[str, int], prices: argparse.Namespace) -> float:
    read = usage.get("cache_read_tokens") or 0
    write = usage.get("cache_write_tokens") or 0
    uncached = max((usage.get("input_tokens") or 0) - read - write, 0)
    return (
        uncached * prices.price_input
        + read * prices.price_cache_read
        + write * prices.price_cache_write
        + (usage.get("output_tokens") or 0) * prices.price_output
    ) / 1_000_000


async def _call(messages: List[Dict[str, Any]], config: ChatConfig) -> Dict[str, Any]:
    started = time.perf_counter()
    result = await llm_call_node({"messages": messages, "metadata": {}}, {"configurable": {"chat_config": config}})
    latency_ms = (time.perf_counter() - started) * 1000
    reply = result["messages"][-1]
    return {
        "reply": reply,
        "messages": result["messages"],
        "latency_ms": latency_ms,
        "usage": reply["metadata"]["usage"],
    }


async def _tool_loop(config: ChatConfig, question: str, use_kb: bool) -> List[Dict[str, Any]]:
    """Run one turn (round 1 -> tool -> round 2); return one record per LLM call."""
    configurable = {
        "chat_config": config.model_copy(update={"enable_rag": use_kb}),
        "kb_store": _KBStore() if use_kb else None,
        "embedding_client": _EmbeddingClient() if use_kb else None,
    }
    rag = await rag_node({"messages": [{"role": "user", "content": question}]}, {"configurable": configurable})
    messages = rag.get("messages") or [{"role": "user", "content": question}]

    records = []
    for round_number in (1, 2):
        call = await _call(messages, config)
        records.append({"round": round_number, "latency_ms": call["latency_ms"], **call["usage"]})
        tool_calls = call["reply"].get("tool_calls") or []
        if not tool_calls:
            break
        messages = call["messages"] + [
            {
                "role": "tool",
                "content": "",
                "tool_results": [
                    {"tool_call_id": tc["id"], "name": tc["name"], "result": get_weather.invoke(tc["args"])}
                    for tc in tool_calls
                ],
            }
        ]
    return records


async def _run_mode(args: argparse.Namespace, caching: bool, stable_prompt: str) -> List[Dict[str, Any]]:
    config = ChatConfig().model_copy(
        update={
            "model_id": args.model,
            "aws_region": args.region,
            "system_prompt": stable_prompt,
            "max_tokens": 300,
            "langchain_tools": [get_weather],
            "fallback_model": None,
            "prompt_caching_enabled": caching,
            "prompt_cache_ttl": args.ttl,
        }
    )
    rows = []
    for run in range(args.warm_runs + 1):
        label = "cold" if run == 0 else f"warm{run}"
        # Retrieval context changes every run; the stable prompt doesn't.
        question = f"What's the weather in Paris? (request {run})"
        for record in await _tool_loop(config, question, use_kb=True):
            record.update(mode="cached" if caching else "baseline", run=label, cost_usd=_cost(record, args))
            rows.append(record)
            print(
                f"{record['mode']:>8} {label:>6} round {record['round']}: {record['latency_ms']:7.0f} ms  "
                f"input={record.get('input_tokens') or 0:>6} read={record.get('cache_read_tokens') or 0:>6} "
                f"write={record.get('cache_write_tokens') or 0:>6} output={record.get('output_tokens') or 0:>4}  "
                f"${record['cost_usd']:.5f}"
            )
    return rows


def _summary(rows: List[Dict[str, Any]], mode: str) -> Dict[str, Any]:
    selected = [r for r in rows if r["mode"] == mode]
    cold = [r for r in selected if r["run"] == "cold"]
    warm = [r for r in selected if r["run"] != "cold"]
    input_tokens = sum(r.get("input_tokens") or 0 for r in warm)
    read = sum(r.get("cache_read_tokens") or 0 for r in warm)
    return {
        "mode": mode,
        "cold_latency_ms": statistics.mean(r["latency_ms"] for r in cold) if cold else None,
        "warm_latency_ms": statistics.mean(r["latency_ms"] for r in warm) if warm else None,
        "warm_hit_rate": read / input_tokens if input_tokens else 0.0,
        "cold_cost_usd": sum(r["cost_usd"] for r in cold),
        "warm_cost_usd_per_run": sum(r["cost_usd"] for r in warm) / max(len({r["run"] for r in warm}), 1),
    }


async def main(argv: List[str]) -> int:
    defaults = ChatConfig()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=defaults.model_id, help="Bedrock model id (default: configured model)")
    parser.add_argument("--region", default=defaults.aws_region, help="AWS region")
    parser.add_argument("--warm-runs", type=int, default=3, help="Warm runs after the cold run (default 3)")
    parser.add_argument("--ttl", choices=["5m", "1h"], default="5m", help="Prompt-cache TTL")
    parser.add_argument("--compare", action="store_true", help="Also run with caching off as a baseline")
    parser.add_argument("--price-input", type=float, default=3.0, help="USD per 1M uncached input tokens")
    parser.add_argument("--price-output", type=float, default=15.0, help="USD per 1M output tokens")
    parser.add_argument("--price-cache-read", type=float, default=0.30, help="USD per 1M cache-read tokens")
    parser.add_argument("--price-cache-write", type=float, default=3.75, help="USD per 1M cache-write tokens")
    parser.add_argument("--json", type=Path, help="Write all rows and the summary to this file")
    args = parser.parse_args(argv)

    if not supports_prompt_caching(args.model):
        print(f"Model {args.model} has no prompt-caching profile in autolangchat.model_capabilities.")
        return 2

    min_tokens = get_prompt_cache_min_tokens(args.model) or 1024
    stable_prompt = _stable_prompt(min_tokens)
    rows = await _run_mode(args, caching=True, stable_prompt=stable_prompt)
    if args.compare:
        # New nonce: the baseline must not benefit from the cached run's prefix.
        rows += await _run_mode(args, caching=False, stable_prompt=_stable_prompt(min_tokens))

    summaries = [_summary(rows, "cached")] + ([_summary(rows, "baseline")] if args.compare else [])
    print()
    for summary in summaries:
        print(json.dumps(summary, indent=2))

    warm_cached = [r for r in rows if r["mode"] == "cached" and r["run"] != "cold"]
    round_two_hits = any(r["round"] == 2 and (r.get("cache_read_tokens") or 0) > 0 for r in warm_cached)
    retrieval_hits = any(r["round"] == 1 and (r.get("cache_read_tokens") or 0) > 0 for r in warm_cached)
    print(f"\nRound 2 of a warm tool loop reads from cache: {'PASS' if round_two_hits else 'FAIL'}")
    print(f"Retrieval turn reads the cached system block:  {'PASS' if retrieval_hits else 'FAIL'}")

    if args.json:
        args.json.write_text(json.dumps({"model": args.model, "rows": rows, "summary": summaries}, indent=2))
        print(f"Wrote {args.json}")
    return 0 if round_two_hits and retrieval_hits else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
