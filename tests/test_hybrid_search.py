#!/usr/bin/env python3
"""
Test script for Hybrid Search (Semantic + BM25).

Tests the hybrid search implementation with various query types:
- Error message queries (exact phrase matching)
- Technical term queries
- Conceptual queries
- Comparison of hybrid vs pure semantic results

Usage:
    python tests/test_hybrid_search.py

Prerequisites:
    - Knowledge base populated with content
    - KB_HYBRID_SEARCH_ENABLED=true (or False to test pure semantic)
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from auto_bedrock_chat_fastapi.bedrock_client import BedrockClient  # noqa: E402
from auto_bedrock_chat_fastapi.config import ChatConfig  # noqa: E402
from auto_bedrock_chat_fastapi.vector_db import VectorDB  # noqa: E402


async def test_error_message_query():
    """Test hybrid search with specific error message"""
    print("\n" + "=" * 80)
    print("Test 1: Error Message Query (Exact Phrase Matching)")
    print("=" * 80)

    query = "RuntimeError: Task attached to a different loop"
    print(f"\nQuery: {query}")

    config = ChatConfig()
    bedrock_client = BedrockClient(config)
    vector_db = VectorDB(config.kb_vector_db_path)

    # Generate embedding
    print("\nGenerating embedding...")
    embedding = await bedrock_client.generate_embedding(text=query, model_id=config.kb_embedding_model)

    # Test pure semantic search
    print("\n--- Pure Semantic Search ---")
    semantic_results = vector_db.semantic_search(query_embedding=embedding, limit=5, min_score=0.0)

    if semantic_results:
        print(f"Found {len(semantic_results)} results")
        for i, result in enumerate(semantic_results[:3], 1):
            print(f"\n{i}. Score: {result['similarity_score']:.4f}")
            print(f"   Document: {result.get('title', 'N/A')}")
            print(f"   Chunk: {result['content'][:150]}...")
            if "RuntimeError" in result["content"]:
                print("   ✅ Contains error message!")
    else:
        print("No results found")

    # Test BM25 search
    print("\n--- BM25 Keyword Search ---")
    bm25_results = vector_db.bm25_search(query=query, limit=5)

    if bm25_results:
        print(f"Found {len(bm25_results)} results")
        for i, result in enumerate(bm25_results[:3], 1):
            print(f"\n{i}. Score: {result['bm25_score']:.4f}")
            print(f"   Document: {result.get('title', 'N/A')}")
            print(f"   Chunk: {result['content'][:150]}...")
            if "RuntimeError" in result["content"]:
                print("   ✅ Contains error message!")
    else:
        print("No results found")

    # Test hybrid search
    print("\n--- Hybrid Search (0.7 Semantic + 0.3 BM25) ---")
    hybrid_results = vector_db.hybrid_search(
        query=query,
        query_embedding=embedding,
        limit=5,
        min_score=0.0,
        semantic_weight=0.7,
        bm25_weight=0.3,
    )

    if hybrid_results:
        print(f"Found {len(hybrid_results)} results")
        for i, result in enumerate(hybrid_results[:3], 1):
            print(f"\n{i}. Hybrid Score: {result['hybrid_score']:.4f}")
            print(f"   - Semantic: {result['semantic_component']:.4f}")
            print(f"   - BM25: {result['bm25_component']:.4f}")
            print(f"   Document: {result.get('title', 'N/A')}")
            print(f"   Chunk: {result['content'][:150]}...")
            if "RuntimeError" in result["content"]:
                print("   ✅ Contains error message!")
    else:
        print("No results found")

    vector_db.close()


async def test_technical_term_query():
    """Test hybrid search with technical terms"""
    print("\n" + "=" * 80)
    print("Test 2: Technical Term Query")
    print("=" * 80)

    query = "AWS Bedrock Converse API authentication"
    print(f"\nQuery: {query}")

    config = ChatConfig()
    bedrock_client = BedrockClient(config)
    vector_db = VectorDB(config.kb_vector_db_path)

    # Generate embedding
    embedding = await bedrock_client.generate_embedding(text=query, model_id=config.kb_embedding_model)

    # Compare results
    semantic_results = vector_db.semantic_search(query_embedding=embedding, limit=3, min_score=0.5)
    hybrid_results = vector_db.hybrid_search(
        query=query,
        query_embedding=embedding,
        limit=3,
        min_score=0.5,
        semantic_weight=0.7,
        bm25_weight=0.3,
    )

    print(f"\nSemantic results: {len(semantic_results)}")
    print(f"Hybrid results: {len(hybrid_results)}")

    print("\n--- Top Hybrid Result ---")
    if hybrid_results:
        top = hybrid_results[0]
        print(
            f"Score: {top['hybrid_score']:.4f} (Semantic: {top['semantic_component']:.4f}, BM25: {top['bm25_component']:.4f})"
        )
        print(f"Title: {top.get('title', 'N/A')}")
        print(f"Content: {top['content'][:300]}...")

    vector_db.close()


async def test_conceptual_query():
    """Test hybrid search with conceptual queries"""
    print("\n" + "=" * 80)
    print("Test 3: Conceptual Query")
    print("=" * 80)

    query = "How do I handle asynchronous operations in web applications?"
    print(f"\nQuery: {query}")

    config = ChatConfig()
    bedrock_client = BedrockClient(config)
    vector_db = VectorDB(config.kb_vector_db_path)

    # Generate embedding
    embedding = await bedrock_client.generate_embedding(text=query, model_id=config.kb_embedding_model)

    # Compare results
    semantic_results = vector_db.semantic_search(query_embedding=embedding, limit=3, min_score=0.5)
    hybrid_results = vector_db.hybrid_search(
        query=query,
        query_embedding=embedding,
        limit=3,
        min_score=0.5,
        semantic_weight=0.7,
        bm25_weight=0.3,
    )

    print(f"\nSemantic results: {len(semantic_results)}")
    print(f"Hybrid results: {len(hybrid_results)}")

    print("\n--- Comparison ---")
    print("Semantic should perform well on conceptual queries")
    print("Hybrid should maintain similar quality with slight boost from keywords")

    if semantic_results and hybrid_results:
        print(f"\nTop Semantic Score: {semantic_results[0]['similarity_score']:.4f}")
        print(f"Top Hybrid Score: {hybrid_results[0]['hybrid_score']:.4f}")

    vector_db.close()


async def test_weighted_scores():
    """Test different weight configurations"""
    print("\n" + "=" * 80)
    print("Test 4: Different Weight Configurations")
    print("=" * 80)

    query = "FastAPI async database connection"
    print(f"\nQuery: {query}")

    config = ChatConfig()
    bedrock_client = BedrockClient(config)
    vector_db = VectorDB(config.kb_vector_db_path)

    # Generate embedding
    embedding = await bedrock_client.generate_embedding(text=query, model_id=config.kb_embedding_model)

    # Test different weight combinations
    weight_configs = [
        (1.0, 0.0, "Pure Semantic"),
        (0.7, 0.3, "Default Hybrid"),
        (0.5, 0.5, "Balanced"),
        (0.3, 0.7, "Keyword-Heavy"),
        (0.0, 1.0, "Pure BM25"),
    ]

    for sem_weight, bm25_weight, label in weight_configs:
        results = vector_db.hybrid_search(
            query=query,
            query_embedding=embedding,
            limit=3,
            min_score=0.0,
            semantic_weight=sem_weight,
            bm25_weight=bm25_weight,
        )

        print(f"\n{label} ({sem_weight:.1f}s + {bm25_weight:.1f}b):")
        if results:
            top = results[0]
            print(f"  Top Score: {top['hybrid_score']:.4f}")
            print(f"  Components - Semantic: {top['semantic_component']:.4f}, BM25: {top['bm25_component']:.4f}")
            print(f"  Title: {top.get('title', 'N/A')[:60]}...")
        else:
            print("  No results")

    vector_db.close()


async def main():
    """Run all hybrid search tests"""
    print("\n" + "=" * 80)
    print("Hybrid Search Test Suite (Semantic + BM25)")
    print("=" * 80)

    config = ChatConfig()
    print("\nConfiguration:")
    print(f"  Vector DB Path: {config.kb_vector_db_path}")
    print(f"  Embedding Model: {config.kb_embedding_model}")
    print(f"  Hybrid Search: {config.kb_hybrid_search_enabled}")
    print(f"  Weights: {config.kb_semantic_weight} semantic + {config.kb_bm25_weight} BM25")

    # Check if DB exists
    if not Path(config.kb_vector_db_path).exists():
        print(f"\n❌ Error: Knowledge base not found at {config.kb_vector_db_path}")
        print("Please populate the knowledge base first.")
        return

    try:
        await test_error_message_query()
        await test_technical_term_query()
        await test_conceptual_query()
        await test_weighted_scores()

        print("\n" + "=" * 80)
        print("✅ All tests completed successfully!")
        print("=" * 80 + "\n")

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
