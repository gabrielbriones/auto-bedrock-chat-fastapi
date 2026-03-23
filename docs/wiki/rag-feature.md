# RAG Feature

The plugin includes a full **Retrieval-Augmented Generation (RAG)** pipeline. You can build a knowledge base from websites or local files, store it in a local SQLite vector database, and have the AI automatically search it before answering user questions.

---

## Architecture

```
Web / Local Files
       │
       ▼
  ContentCrawler          # Fetches and parses content
       │
       ▼
  EmbeddingPipeline       # Chunks text → Bedrock Titan embeddings (1536d)
       │
       ▼
  VectorDB (SQLite-vec)   # Stores documents, chunks, and vector embeddings
       │
       ▼  (on every user message)
  Semantic Search         # Cosine similarity lookup → top-K chunks
       │
       ▼
  System Prompt Injection # Relevant chunks added to AI context
       │
       ▼
  AI Response             # Grounded in knowledge base content
```

---

## Quick Start

### 1. Install Dependencies

```bash
poetry install   # includes sqlite-vec, beautifulsoup4, aiohttp
```

### 2. Build a Knowledge Base

```python
import asyncio
from auto_bedrock_chat_fastapi.content_crawler import ContentCrawler
from auto_bedrock_chat_fastapi.embedding_pipeline import EmbeddingPipeline
from auto_bedrock_chat_fastapi.vector_db import VectorDB
from auto_bedrock_chat_fastapi.bedrock_client import BedrockClient
from auto_bedrock_chat_fastapi.config import ChatConfig

async def build_kb():
    config = ChatConfig()
    client = BedrockClient(config)
    db = VectorDB("knowledge_base.db")
    crawler = ContentCrawler()
    pipeline = EmbeddingPipeline(bedrock_client=client, vector_db=db)

    # Crawl a website
    documents = await crawler.crawl_url(
        url="https://docs.example.com/",
        source="docs",
        topic="documentation",
        max_depth=2
    )

    # Build embeddings and store in DB
    await pipeline.process_documents(documents)
    print(f"Indexed {len(documents)} documents")

asyncio.run(build_kb())
```

### 3. Attach to the Plugin

```python
from fastapi import FastAPI
from auto_bedrock_chat_fastapi import add_bedrock_chat
from auto_bedrock_chat_fastapi.vector_db import VectorDB

app = FastAPI()
db = VectorDB("knowledge_base.db")

bedrock_chat = add_bedrock_chat(app, vector_db=db)
```

---

## ContentCrawler

Fetches and parses web pages or local Markdown files.

```python
from auto_bedrock_chat_fastapi.content_crawler import ContentCrawler

crawler = ContentCrawler(
    max_concurrent=5,      # concurrent requests
    rate_limit_delay=1.0,  # seconds between requests
    timeout=30
)

# Crawl a single URL
docs = await crawler.crawl_url(
    url="https://docs.example.com/guide",
    source="guide",
    topic="quickstart"
)

# Crawl an entire site (recursive)
docs = await crawler.crawl_site(
    base_url="https://docs.example.com/",
    max_depth=3,
    max_pages=200,
    allowed_domains=["docs.example.com"]
)

# Load local Markdown files
docs = crawler.load_local_files(
    directory="./docs",
    glob_pattern="**/*.md",
    source="internal-docs"
)
```

---

## EmbeddingPipeline

Chunks documents and generates vector embeddings via AWS Bedrock Titan.

```python
from auto_bedrock_chat_fastapi.embedding_pipeline import EmbeddingPipeline, TextChunker

chunker = TextChunker(
    chunk_size=512,    # tokens per chunk
    chunk_overlap=100, # overlap between chunks
    min_chunk_size=50  # minimum words
)

pipeline = EmbeddingPipeline(
    bedrock_client=client,
    vector_db=db,
    chunker=chunker,
    model="amazon.titan-embed-text-v1",  # 1536 dimensions
    batch_size=25,
    cache_dir=".embedding_cache"  # avoids re-embedding unchanged content
)

await pipeline.process_documents(documents)
```

**Supported embedding models:**

| Model                          | Dimensions     |
| ------------------------------ | -------------- |
| `amazon.titan-embed-text-v1`   | 1536 (default) |
| `amazon.titan-embed-text-v2:0` | Configurable   |
| `cohere.embed-english-v3`      | 1024           |

---

## VectorDB

SQLite-based vector store for semantic similarity search.

```python
from auto_bedrock_chat_fastapi.vector_db import VectorDB

db = VectorDB("knowledge_base.db")

# Add a document manually
db.add_document(
    doc_id="guide-001",
    content="Full document text...",
    title="Getting Started",
    source="docs",
    source_url="https://docs.example.com/guide",
    topic="quickstart"
)

# Semantic search
results = db.semantic_search(
    query_embedding=embedding_vector,
    limit=5,
    min_score=0.7,
    filters={"source": "docs", "topic": "quickstart"}
)

# Get stats
stats = db.get_stats()
print(f"Documents: {stats['total_documents']}, Chunks: {stats['total_chunks']}")
```

### Database Schema

| Table        | Contents                                                |
| ------------ | ------------------------------------------------------- |
| `documents`  | Full documents with metadata (source, topic, URL, date) |
| `chunks`     | Text chunks with position info                          |
| `vec_chunks` | Vector embeddings (virtual table via sqlite-vec)        |

---

## YAML Knowledge Base Configuration

For repeatable builds, define sources in a YAML file:

```yaml
# kb_sources.yaml
sources:
  - url: "https://docs.example.com/tutorial"
    source: "tutorial"
    topic: "getting-started"
    max_depth: 2

  - url: "https://docs.example.com/reference"
    source: "reference"
    topic: "api"
    max_depth: 1
```

```python
pipeline.build_from_yaml("kb_sources.yaml")
```

---

## Hybrid Search (RAG + Tool Calling)

You can combine RAG context injection with tool calling:

- **RAG** automatically injects relevant chunks into the system prompt before each LLM call
- **Tool calling** lets the AI explicitly query the KB via a `/knowledge/search` endpoint

This hybrid approach gives both reliability (RAG always provides context) and transparency (explicit tool calls visible in the UI).

See `examples/fastAPI/app_rag.py` for a complete hybrid search example.

---

## REST Semantic Search Endpoint

When a `VectorDB` is attached, the plugin exposes:

```http
POST /bedrock-chat/semantic-search
Content-Type: application/json

{
  "query": "how do I authenticate?",
  "limit": 5,
  "min_score": 0.7,
  "filters": {
    "source": "docs",
    "topic": "auth"
  }
}
```

---

## See Also

- [Architecture](architecture.md) — how RAG fits in the overall system
- [Token Management](token-management.md) — handling large KB results
- `examples/fastAPI/app_rag.py` — complete RAG example
- `docs/kb-architecture-diagrams.html` — visual comparison of RAG vs Tool Calling vs Hybrid
