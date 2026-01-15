# Embedding Pipeline Setup

Complete guide for using the embedding pipeline with AWS Bedrock.

## Table of Contents

- [Overview](#overview)
- [Components](#components)
- [Quick Start](#quick-start)
- [API Reference](#api-reference)
- [Configuration](#configuration)
- [Examples](#examples)
- [Best Practices](#best-practices)
- [Troubleshooting](#troubleshooting)

## Overview

The embedding pipeline converts documents and text into vector embeddings using AWS Bedrock's Titan Embed models. It provides:

- **Text Chunking**: Split documents into manageable pieces (512 tokens with 100 token overlap)
- **Embedding Generation**: Convert text to vectors using AWS Bedrock Titan models
- **Caching**: Avoid re-processing with JSON-based embedding cache
- **Batch Processing**: Efficient concurrent embedding generation (25 requests/batch)

### Architecture

```
Document → TextChunker → EmbeddingGenerator → Vector DB
              ↓              ↓
         Chunks (512t)   Embeddings (1536d)
```

## Components

### 1. TextChunker

Splits text into overlapping chunks for better semantic coverage.

```python
from auto_bedrock_chat_fastapi.embedding_pipeline import TextChunker

chunker = TextChunker(
    chunk_size=512,      # tokens (approx)
    chunk_overlap=100,   # tokens
    min_chunk_size=50    # minimum words
)
```

**Key Features:**

- Preserves paragraph structure (optional)
- Maintains metadata across chunks
- Filters out tiny chunks
- Token-aware splitting

### 2. EmbeddingGenerator

Generates embeddings using AWS Bedrock Titan models.

```python
from auto_bedrock_chat_fastapi.embedding_pipeline import EmbeddingGenerator
from auto_bedrock_chat_fastapi.bedrock_client import BedrockClient
from auto_bedrock_chat_fastapi.config import ChatConfig

config = ChatConfig()
bedrock_client = BedrockClient(config)

generator = EmbeddingGenerator(
    bedrock_client=bedrock_client,
    model="amazon.titan-embed-text-v1",  # 1536 dimensions
    cache_dir=".embedding_cache",
    batch_size=25
)
```

**Supported Models:**

- `amazon.titan-embed-text-v1` - 1536 dimensions (default)
- `amazon.titan-embed-text-v2:0` - Configurable dimensions
- `cohere.embed-english-v3` - Cohere English embeddings
- `cohere.embed-multilingual-v3` - Cohere multilingual embeddings

### 3. EmbeddingPipeline

End-to-end document processing (chunk + embed).

```python
from auto_bedrock_chat_fastapi.embedding_pipeline import EmbeddingPipeline

pipeline = EmbeddingPipeline(
    chunker=chunker,
    generator=generator
)
```

## Quick Start

### Basic Usage

```python
import asyncio
from auto_bedrock_chat_fastapi.bedrock_client import BedrockClient
from auto_bedrock_chat_fastapi.config import ChatConfig
from auto_bedrock_chat_fastapi.embedding_pipeline import (
    TextChunker,
    EmbeddingGenerator,
    EmbeddingPipeline
)

# Initialize
config = ChatConfig()
bedrock_client = BedrockClient(config)

chunker = TextChunker()
generator = EmbeddingGenerator(bedrock_client=bedrock_client)
pipeline = EmbeddingPipeline(chunker=chunker, generator=generator)

# Process document
document = {
    'id': 'doc-001',
    'title': 'My Document',
    'content': 'Your document content here...',
    'source': 'docs',
    'topic': 'tutorial'
}

chunks = pipeline.process_document(document)

# Each chunk has: chunk_id, text, embedding, word_count, metadata
for chunk in chunks:
    print(f"Chunk {chunk['chunk_id']}: {chunk['word_count']} words")
    print(f"Embedding dimensions: {len(chunk['embedding'])}")
```

### With Vector Database

```python
from auto_bedrock_chat_fastapi.vector_db import VectorDB

vector_db = VectorDB("knowledge_base.db")

# Add document
vector_db.add_document(
    doc_id=document['id'],
    content=document['content'],
    title=document['title'],
    source=document['source'],
    source_url=document.get('url', ''),
    topic=document.get('topic')
)

# Add chunks
for chunk in chunks:
    chunk_id = f"{document['id']}_chunk_{chunk['chunk_id']}"
    vector_db.add_chunk(
        chunk_id=chunk_id,
        doc_id=document['id'],
        text=chunk['text'],
        embedding=chunk['embedding'],
        chunk_index=chunk['chunk_id']
    )

# Semantic search
query_embedding = await bedrock_client.generate_embedding("How do I install?")
results = vector_db.semantic_search(query_embedding, limit=5)
```

## API Reference

### TextChunker

#### `chunk_text(text: str, metadata: Optional[Dict] = None) -> List[Dict]`

Chunk plain text into overlapping segments.

**Parameters:**

- `text`: Text to chunk
- `metadata`: Optional metadata to include in each chunk

**Returns:**
List of chunk dictionaries:

```python
{
    'chunk_id': 0,              # Sequential chunk number
    'text': '...',              # Chunk text content
    'word_count': 512,          # Number of words
    'start_word': 0,            # Start position (words)
    'end_word': 512,            # End position (words)
    'is_continuation': False,   # True if not first chunk
    'metadata': {...}           # Optional metadata
}
```

#### `chunk_document(document: Dict, preserve_structure: bool = True) -> List[Dict]`

Chunk a full document with metadata preservation.

**Parameters:**

- `document`: Document dictionary with `content`, `id`, `title`, etc.
- `preserve_structure`: Try to split on paragraph boundaries

**Returns:**
List of chunk dictionaries (same format as `chunk_text`)

### EmbeddingGenerator

#### `generate_embedding(text: str) -> List[float]`

Generate embedding for a single text.

**Parameters:**

- `text`: Text to embed

**Returns:**

- List of floats (1536-dim for Titan v1)

**Notes:**

- Automatically checks cache first
- Handles both running and non-running event loops
- Saves to cache if cache_dir is configured

#### `generate_embeddings_batch(texts: List[str], show_progress: bool = True) -> List[List[float]]`

Generate embeddings for multiple texts in batch.

**Parameters:**

- `texts`: List of texts to embed
- `show_progress`: Show progress bar (default: True)

**Returns:**

- List of embeddings (each is a list of floats)

**Notes:**

- Processes in parallel (batch_size requests at a time)
- Uses cache for previously seen texts
- Returns zero-vector for failed embeddings

### EmbeddingPipeline

#### `process_document(document: Dict, preserve_structure: bool = True) -> List[Dict]`

Process document end-to-end (chunk + embed).

**Parameters:**

- `document`: Document dictionary
- `preserve_structure`: Preserve paragraph boundaries

**Returns:**
List of chunk dictionaries with embeddings:

```python
{
    'chunk_id': 0,
    'text': '...',
    'embedding': [0.1, 0.2, ...],  # 1536-dim vector
    'word_count': 512,
    'metadata': {...}
}
```

## Configuration

### Chunking Parameters

| Parameter        | Default | Description                       |
| ---------------- | ------- | --------------------------------- |
| `chunk_size`     | 512     | Target tokens per chunk           |
| `chunk_overlap`  | 100     | Overlapping tokens between chunks |
| `min_chunk_size` | 50      | Minimum words per chunk           |

**Recommendations:**

- **Small docs** (< 1000 words): `chunk_size=256, overlap=50`
- **Medium docs** (1000-5000 words): `chunk_size=512, overlap=100` (default)
- **Large docs** (> 5000 words): `chunk_size=1024, overlap=200`
- **Conversational**: `chunk_size=128, overlap=20`

### Embedding Models

| Model                          | Dimensions   | Use Case                  |
| ------------------------------ | ------------ | ------------------------- |
| `amazon.titan-embed-text-v1`   | 1536         | General purpose (default) |
| `amazon.titan-embed-text-v2:0` | Configurable | Flexible dimensions       |
| `cohere.embed-english-v3`      | 1024         | English-only content      |
| `cohere.embed-multilingual-v3` | 1024         | Multilingual content      |

### Caching

Enable caching to avoid re-processing:

```python
generator = EmbeddingGenerator(
    bedrock_client=bedrock_client,
    cache_dir=".embedding_cache"  # Enable cache
)
```

**Cache Location:**

- Default: No caching
- Recommended: `.embedding_cache/` (project root)
- Production: `/var/cache/embeddings/` or S3

**Cache Format:**

```json
{
    "text": "Original text",
    "model": "amazon.titan-embed-text-v1",
    "embedding": [0.1, 0.2, ...],
    "timestamp": "2025-01-15T10:30:00Z"
}
```

**Cache Key:** `SHA256(text + model)`

## Examples

### Example 1: Single Embedding

```python
import asyncio
from auto_bedrock_chat_fastapi.bedrock_client import BedrockClient
from auto_bedrock_chat_fastapi.config import ChatConfig

async def main():
    config = ChatConfig()
    bedrock_client = BedrockClient(config)

    text = "FastAPI is a modern web framework"
    embedding = await bedrock_client.generate_embedding(text)

    print(f"Dimensions: {len(embedding)}")
    print(f"First 5 values: {embedding[:5]}")

asyncio.run(main())
```

### Example 2: Batch Embeddings

```python
import asyncio
from auto_bedrock_chat_fastapi.bedrock_client import BedrockClient
from auto_bedrock_chat_fastapi.config import ChatConfig

async def main():
    config = ChatConfig()
    bedrock_client = BedrockClient(config)

    texts = [
        "FastAPI is modern",
        "AWS Bedrock provides foundation models",
        "Vector databases enable semantic search"
    ]

    embeddings = await bedrock_client.generate_embeddings_batch(texts)

    print(f"Generated {len(embeddings)} embeddings")

asyncio.run(main())
```

### Example 3: Document Processing

```python
from auto_bedrock_chat_fastapi.embedding_pipeline import EmbeddingPipeline
# ... (initialize as in Quick Start)

document = {
    'id': 'doc-001',
    'content': 'Long document content...',
    'title': 'Tutorial'
}

chunks = pipeline.process_document(document)

for chunk in chunks:
    print(f"Chunk {chunk['chunk_id']}: {chunk['word_count']} words")
```

### Example 4: Populate Knowledge Base

```python
from auto_bedrock_chat_fastapi.content_crawler import LocalContentLoader

# Load local docs
loader = LocalContentLoader()
documents = loader.load_directory("docs", source="docs", pattern="*.md")

# Process each
for doc in documents:
    chunks = pipeline.process_document(doc)

    # Add to vector DB
    vector_db.add_document(
        doc_id=doc['id'],
        content=doc['content'],
        title=doc['title'],
        source=doc['source'],
        source_url=doc['url']
    )

    for chunk in chunks:
        vector_db.add_chunk(
            chunk_id=f"{doc['id']}_chunk_{chunk['chunk_id']}",
            doc_id=doc['id'],
            text=chunk['text'],
            embedding=chunk['embedding'],
            chunk_index=chunk['chunk_id']
        )
```

### Example 5: Semantic Search

```python
import asyncio

async def search_knowledge_base(query: str):
    # Generate query embedding
    query_embedding = await bedrock_client.generate_embedding(query)

    # Search vector DB
    results = vector_db.semantic_search(
        query_embedding=query_embedding,
        limit=5,
        min_score=0.7
    )

    # Display results
    for i, result in enumerate(results):
        print(f"{i+1}. Score: {result['score']:.3f}")
        print(f"   From: {result['title']}")
        print(f"   {result['text'][:100]}...")

asyncio.run(search_knowledge_base("How do I install FastAPI?"))
```

## Best Practices

### 1. Chunking Strategy

**Do:**

- Use overlap to preserve context across boundaries
- Keep chunks between 200-1000 tokens
- Preserve paragraph structure for narrative content
- Use smaller chunks for Q&A, larger for documents

**Don't:**

- Split mid-sentence (use `preserve_structure=True`)
- Create tiny chunks (< 50 words)
- Use huge chunks (> 2000 tokens) - context gets diluted

### 2. Embedding Generation

**Do:**

- Enable caching for repeated content
- Use batch processing for multiple texts
- Handle event loops correctly (see examples)
- Monitor AWS Bedrock quotas and costs

**Don't:**

- Embed empty or trivial text
- Process same content multiple times (use cache)
- Ignore rate limiting errors
- Forget to handle embedding generation failures

### 3. Performance Optimization

```python
# Good: Batch processing with cache
generator = EmbeddingGenerator(
    bedrock_client=bedrock_client,
    cache_dir=".embedding_cache",
    batch_size=25  # Adjust based on rate limits
)

# Bad: One-by-one without cache
for text in texts:
    embedding = generator.generate_embedding(text)  # Slow!
```

### 4. Error Handling

```python
try:
    chunks = pipeline.process_document(document)
except Exception as e:
    logger.error(f"Failed to process document {document['id']}: {e}")
    # Fall back to smaller chunks or skip
```

### 5. Testing

```python
# Test with small sample first
sample_docs = documents[:3]
for doc in sample_docs:
    chunks = pipeline.process_document(doc)
    assert len(chunks) > 0
    assert all('embedding' in chunk for chunk in chunks)
```

## Troubleshooting

### Issue: "No chunks generated"

**Cause:** Document too short (< min_chunk_size)

**Solution:**

```python
chunker = TextChunker(min_chunk_size=10)  # Lower minimum
```

### Issue: "Embedding generation failed"

**Cause:** AWS credentials not configured or rate limit hit

**Solution:**

1. Check AWS credentials: `aws configure`
2. Verify Bedrock access: `aws bedrock list-foundation-models`
3. Check logs for specific error
4. Reduce batch_size if hitting rate limits

### Issue: "Out of memory"

**Cause:** Processing too many embeddings at once

**Solution:**

```python
# Process in smaller batches
batch_size = 100
for i in range(0, len(documents), batch_size):
    batch = documents[i:i+batch_size]
    for doc in batch:
        chunks = pipeline.process_document(doc)
        # Store immediately
```

### Issue: "Cache not working"

**Cause:** Cache directory not created or permissions issue

**Solution:**

```python
from pathlib import Path

cache_dir = Path(".embedding_cache")
cache_dir.mkdir(exist_ok=True)  # Ensure it exists

generator = EmbeddingGenerator(
    bedrock_client=bedrock_client,
    cache_dir=str(cache_dir)  # Must be string
)
```

### Issue: "Event loop errors"

**Cause:** Mixing sync/async code incorrectly

**Solution:**

```python
# In async context - use await
embedding = await bedrock_client.generate_embedding(text)

# In sync context - use generator wrapper
embedding = generator.generate_embedding(text)  # Handles event loop
```

### Debug Logging

Enable detailed logging:

```python
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Run your code
chunks = pipeline.process_document(document)
```

## Performance Benchmarks

Tested on AWS us-east-1 with Titan Embed v1:

| Operation                     | Time   | Notes                      |
| ----------------------------- | ------ | -------------------------- |
| Single embedding              | ~150ms | Including network overhead |
| Batch (25 texts)              | ~2s    | Parallel processing        |
| Chunk 1000-word doc           | ~50ms  | CPU-bound                  |
| Full pipeline (1000-word doc) | ~3s    | 2 chunks, cached           |
| Cache hit                     | ~1ms   | Local file read            |

**Tips:**

- Use batch processing for > 10 texts
- Enable caching for repeated content
- Consider pre-computing embeddings for static content
- Monitor AWS Bedrock costs (~$0.0001 per 1000 tokens)

## Next Steps

1. **Test Setup**: Run `examples/embedding_examples.py`
2. **Populate KB**: Load your documents and generate embeddings
3. **Implement Search**: Add semantic search endpoint
4. **Integrate RAG**: Inject KB chunks into prompts

See also:

- [Vector Database Setup](VECTOR_DB_SETUP.md) (if it exists)
- [Web Crawler Setup](WEB_CRAWLER_SETUP.md)
- [Authentication Guide](AUTHENTICATION_QUICK_START.md)
