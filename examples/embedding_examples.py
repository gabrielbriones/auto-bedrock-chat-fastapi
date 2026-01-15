"""Example script showing how to use the embedding pipeline with AWS Bedrock."""

import asyncio
import logging

from auto_bedrock_chat_fastapi.bedrock_client import BedrockClient
from auto_bedrock_chat_fastapi.config import ChatConfig
from auto_bedrock_chat_fastapi.content_crawler import LocalContentLoader
from auto_bedrock_chat_fastapi.embedding_pipeline import EmbeddingGenerator, EmbeddingPipeline, TextChunker
from auto_bedrock_chat_fastapi.vector_db import VectorDB

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%H:%M:%S"
)


async def example_single_embedding():
    """Example: Generate a single embedding using AWS Bedrock Titan."""
    print("\n" + "=" * 80)
    print("Example 1: Single Embedding Generation")
    print("=" * 80)

    # Initialize Bedrock client
    config = ChatConfig()
    bedrock_client = BedrockClient(config)

    # Generate embedding
    text = "FastAPI is a modern, fast web framework for building APIs with Python."
    embedding = await bedrock_client.generate_embedding(text=text, model_id="amazon.titan-embed-text-v1")

    print(f"\nText: {text}")
    print(f"Embedding dimensions: {len(embedding)}")
    print(f"First 5 values: {embedding[:5]}")


async def example_batch_embeddings():
    """Example: Generate embeddings for multiple texts in batch."""
    print("\n" + "=" * 80)
    print("Example 2: Batch Embedding Generation")
    print("=" * 80)

    # Initialize Bedrock client
    config = ChatConfig()
    bedrock_client = BedrockClient(config)

    # Multiple texts
    texts = [
        "FastAPI is a modern web framework for Python.",
        "AWS Bedrock provides access to foundation models.",
        "Vector databases enable semantic search capabilities.",
        "Retrieval-Augmented Generation improves AI accuracy.",
    ]

    # Generate embeddings in batch
    embeddings = await bedrock_client.generate_embeddings_batch(
        texts=texts, model_id="amazon.titan-embed-text-v1", batch_size=2  # Process 2 at a time
    )

    print(f"\nGenerated {len(embeddings)} embeddings")
    for i, (text, emb) in enumerate(zip(texts, embeddings)):
        print(f"{i+1}. {text[:50]}... → {len(emb)} dims")


async def example_chunk_and_embed():
    """Example: Chunk text and generate embeddings."""
    print("\n" + "=" * 80)
    print("Example 3: Text Chunking and Embedding")
    print("=" * 80)

    # Initialize components
    config = ChatConfig()
    bedrock_client = BedrockClient(config)

    chunker = TextChunker(
        chunk_size=256, chunk_overlap=50, min_chunk_size=30  # tokens (approx)  # tokens  # minimum words
    )

    generator = EmbeddingGenerator(
        bedrock_client=bedrock_client, model="amazon.titan-embed-text-v1", cache_dir=".embedding_cache", batch_size=10
    )

    _ = EmbeddingPipeline(chunker=chunker, generator=generator)

    # Sample document
    document = {
        "id": "doc-001",
        "title": "FastAPI Tutorial",
        "content": """FastAPI is a modern, fast (high-performance) web framework for building APIs
        with Python 3.7+ based on standard Python type hints. The key features are: Fast to code,
        with great editor support; reduces bugs; easy to use and learn; ready for production.

        FastAPI provides automatic interactive API documentation, data validation using Pydantic,
        async support for high performance, and dependency injection for clean architecture.

        You can build APIs quickly with minimal code while maintaining high performance and
        automatic data validation. It's one of the fastest Python frameworks available.""",
        "source": "docs",
        "topic": "web-framework",
    }

    # Chunk the document
    chunks_data = chunker.chunk_document(document, preserve_structure=True)

    # Generate embeddings for all chunks (async)
    texts = [chunk["text"] for chunk in chunks_data]
    embeddings = await bedrock_client.generate_embeddings_batch(
        texts=texts, model_id="amazon.titan-embed-text-v1", batch_size=10
    )

    # Combine chunks with embeddings
    chunks = []
    for chunk_data, embedding in zip(chunks_data, embeddings):
        chunk_data["embedding"] = embedding
        chunks.append(chunk_data)

    print(f"\nDocument: {document['title']}")
    print(f"Total chunks: {len(chunks)}")
    print("\nChunk details:")
    for chunk in chunks:
        print(
            f"  - Chunk {chunk['chunk_id']}: {chunk['word_count']} words, " f"{len(chunk['embedding'])} dim embedding"
        )
        print(f"    Preview: {chunk['text'][:80]}...")


async def example_populate_kb_from_local_files():
    """Example: Load local docs, chunk, embed, and store in vector DB."""
    print("\n" + "=" * 80)
    print("Example 4: Populate Knowledge Base from Local Files")
    print("=" * 80)

    # Clean up old database if it exists
    import os

    db_path = "knowledge_base.db"
    if os.path.exists(db_path):
        os.remove(db_path)
        print("Cleaned up existing database\n")

    # Initialize components
    config = ChatConfig()
    bedrock_client = BedrockClient(config)
    vector_db = VectorDB(db_path)

    # Load local markdown files
    loader = LocalContentLoader()
    documents = loader.load_directory(
        dir_path="docs", source="docs", pattern="*.md"  # Only root-level .md files for this example
    )

    print(f"Loaded {len(documents)} documents")

    # Set up embedding pipeline
    chunker = TextChunker(chunk_size=512, chunk_overlap=100)
    generator = EmbeddingGenerator(
        bedrock_client=bedrock_client, model="amazon.titan-embed-text-v1", cache_dir=".embedding_cache"
    )
    _ = EmbeddingPipeline(chunker=chunker, generator=generator)

    # Process first 3 documents as example (to avoid long runtime)
    sample_docs = documents[:3]

    for doc in sample_docs:
        print(f"\nProcessing: {doc['title']}")

        # Chunk the document
        chunks_data = chunker.chunk_document(doc, preserve_structure=True)

        if not chunks_data:
            print("  Skipping - no chunks generated")
            continue

        # Generate embeddings for all chunks (async)
        texts = [chunk["text"] for chunk in chunks_data]
        embeddings = await bedrock_client.generate_embeddings_batch(
            texts=texts, model_id="amazon.titan-embed-text-v1", batch_size=25
        )

        # Combine chunks with embeddings
        chunks = []
        for chunk_data, embedding in zip(chunks_data, embeddings):
            chunk_data["embedding"] = embedding
            chunks.append(chunk_data)

        # Add document to vector DB
        vector_db.add_document(
            doc_id=doc["id"],
            content=doc["content"],
            title=doc["title"],
            source=doc["source"],
            source_url=doc["url"],
            topic=doc.get("topic"),
            metadata={"word_count": doc["word_count"]},
        )

        # Add chunks with embeddings
        for chunk in chunks:
            chunk_id = f"{doc['id']}_chunk_{chunk['chunk_id']}"
            vector_db.add_chunk(
                chunk_id=chunk_id,
                document_id=doc["id"],
                content=chunk["text"],
                embedding=chunk["embedding"],
                chunk_index=chunk["chunk_id"],
            )

        print(f"  Added {len(chunks)} chunks to vector DB")

    # Show stats
    stats = vector_db.get_stats()
    print("\n📊 Vector DB Stats:")
    print(f"  Documents: {stats['documents']}")
    print(f"  Chunks: {stats['chunks']}")
    print(f"  Vectors: {stats['vectors']}")

    # Test semantic search
    print("\n🔍 Testing Semantic Search:")
    query = "How does authentication work?"
    query_embedding = await bedrock_client.generate_embedding(query)

    results = vector_db.semantic_search(query_embedding=query_embedding, limit=3, min_score=0.5)

    print(f"Query: '{query}'")
    print(f"Results: {len(results)}")
    for i, result in enumerate(results):
        print(f"\n  {i+1}. Score: {result['similarity_score']:.3f}")
        print(f"     From: {result['title']}")
        print(f"     Preview: {result['content'][:100]}...")

    vector_db.close()


if __name__ == "__main__":
    print("AWS Bedrock Embedding Pipeline Examples")
    print("=" * 80)
    print("\nThese examples show how to use AWS Bedrock Titan models for embeddings.")
    print("Make sure your AWS credentials are configured!")

    # Uncomment the example you want to run:

    asyncio.run(example_single_embedding())
    asyncio.run(example_batch_embeddings())
    asyncio.run(example_chunk_and_embed())
    asyncio.run(example_populate_kb_from_local_files())

    print("\n" + "=" * 80)
    print("✅ Examples completed!")
