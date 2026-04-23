# RAG Feature

The plugin includes a full **Retrieval-Augmented Generation (RAG)** pipeline. You can build a knowledge base from websites or local files, store it in a vector database, and have the AI automatically search it before answering user questions.

Two storage backends are supported:

- **SQLite** (default) — zero-config, great for development and single-instance deployments
- **PostgreSQL + pgvector** — production-ready, supports concurrent access, horizontal scaling, and persistent storage

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
  BaseKBStore             # Abstract interface for KB storage
   ┌───┴───┐
   │       │
SQLiteKB  PgVectorKB      # SQLite-vec / PostgreSQL+pgvector
   Store   Store
       │
       ▼  (on every user message)
  Hybrid Search           # Semantic + keyword search → top-K chunks
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
# Core (includes SQLite backend)
poetry install

# With PostgreSQL backend
poetry install --extras postgres
# or: pip install ".[postgres]"
```

### 2. Build a Knowledge Base

```python
import asyncio
from auto_bedrock_chat_fastapi.content_crawler import ContentCrawler
from auto_bedrock_chat_fastapi.embedding_pipeline import EmbeddingPipeline
from auto_bedrock_chat_fastapi.kb_store_base import create_kb_store
from auto_bedrock_chat_fastapi.bedrock_client import BedrockClient
from auto_bedrock_chat_fastapi.config import ChatConfig

async def build_kb():
    config = ChatConfig()
    client = BedrockClient(config)
    db = create_kb_store(config)  # uses kb_storage_type from config
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

app = FastAPI()

# The plugin creates the appropriate KB store based on config:
#   BEDROCK_KB_STORAGE_TYPE=sqlite   → uses SQLite (default)
#   BEDROCK_KB_STORAGE_TYPE=pgvector → uses PostgreSQL+pgvector
bedrock_chat = add_bedrock_chat(app)
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

## Storage Backends

The KB storage layer is abstracted behind `BaseKBStore`. A factory function `create_kb_store(config)` creates the right implementation based on the `BEDROCK_KB_STORAGE_TYPE` setting.

### Backend Comparison

| Feature            | SQLite                          | PostgreSQL + pgvector              |
| ------------------ | ------------------------------- | ---------------------------------- |
| Setup              | Zero-config                     | Requires PostgreSQL server         |
| Concurrent writes  | Single writer                   | Full MVCC concurrency              |
| Horizontal scaling | No (file-based)                 | Yes (shared database)              |
| Persistence        | Volume mount required in Docker | Built-in                           |
| Connection pooling | No                              | Yes (configurable pool size)       |
| Vector search      | `sqlite-vec` cosine similarity  | pgvector HNSW cosine distance      |
| Full-text search   | FTS5 (BM25)                     | `tsvector` + GIN index (`ts_rank`) |
| Best for           | Development, single-instance    | Production, multi-instance         |

### SQLite (default)

No configuration needed — works out of the box.

```bash
# .env
BEDROCK_KB_STORAGE_TYPE=sqlite    # default
KB_DATABASE_PATH=data/knowledge_base.db
```

**Limitations:** single-writer, no concurrent access from multiple app instances, data lives on the filesystem.

### PostgreSQL + pgvector

Requires PostgreSQL with the [pgvector](https://github.com/pgvector/pgvector) extension. The plugin auto-creates tables and indexes on first connection.

```bash
# .env
BEDROCK_KB_STORAGE_TYPE=pgvector
BEDROCK_KB_POSTGRES_URL=postgresql://kb_user:kb_password@localhost:5432/knowledge_base
BEDROCK_KB_POSTGRES_POOL_SIZE=5
BEDROCK_KB_EMBEDDING_DIMENSIONS=1536
```

Install the optional dependency:

```bash
pip install "auto-bedrock-chat-fastapi[postgres]"
# or: poetry install --extras postgres
```

### Docker Compose

The default `docker-compose.yml` runs both the app and PostgreSQL+pgvector:

```bash
# PostgreSQL mode (default)
docker compose up --build

# Populate the knowledge base
docker compose exec bedrock-chat-api \
  python -m auto_bedrock_chat_fastapi.commands.kb populate
```

For SQLite-only mode (no PostgreSQL container):

```bash
docker compose -f docker-compose.yml -f docker-compose.sqlite.yml up --build
```

### Production Recommendations

- **Connection pool size:** Start with 5 (default). Increase to 10–20 for high-traffic deployments. Each app instance maintains its own pool.
- **Embedding dimensions:** Must match your embedding model output. Default is 1536 (Amazon Titan Embed Text v1).
- **Backups:** Use `pg_dump` or continuous archiving. The pgvector data is stored in regular PostgreSQL tables.
- **Monitoring:** Standard PostgreSQL monitoring applies. Watch connection pool utilization and query latency on the `chunks` table.

---

## VectorDB (legacy alias)

The `VectorDB` class in `vector_db.py` is a backward-compatible alias for `SQLiteKBStore`. It emits a `DeprecationWarning` on instantiation. New code should use `create_kb_store(config)` or `SQLiteKBStore` directly.

```python
# Legacy (deprecated)
from auto_bedrock_chat_fastapi.vector_db import VectorDB
db = VectorDB("knowledge_base.db")

# Preferred
from auto_bedrock_chat_fastapi.kb_store_base import create_kb_store
db = create_kb_store(config)
```

### Database Schema

| Table        | SQLite                                                  | PostgreSQL                                      |
| ------------ | ------------------------------------------------------- | ----------------------------------------------- |
| `documents`  | Full documents with metadata (source, topic, URL, date) | Same columns                                    |
| `chunks`     | Text chunks with position info                          | Same columns + `content_tsv` generated column   |
| `vec_chunks` | Vector embeddings (virtual table via `sqlite-vec`)      | `embedding vector(N)` column on `chunks` table  |
| Indexes      | `sqlite-vec` cosine index, FTS5                         | HNSW (`vector_cosine_ops`), GIN (`content_tsv`) |

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

When RAG is enabled (`ENABLE_RAG=true`), the plugin exposes:

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
