# Vector Database Setup Guide

## Overview

This project uses **SQLite with sqlite-vec extension** for vector similarity search. This provides a simple, file-based solution perfect for MVP and development phases with easy migration to production vector databases if needed.

## Installation

```bash
poetry add sqlite-vec
poetry add "numpy>=1.21,<2.0"
```

## Database Schema

### Tables

#### `documents`

Stores complete documents with metadata.

```sql
CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    title TEXT,
    source TEXT,              -- e.g., "docs", "blog", "faq"
    source_url TEXT,
    topic TEXT,
    date_published TEXT,      -- ISO format: YYYY-MM-DD
    metadata TEXT,            -- JSON string
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

#### `chunks`

Stores document chunks for embedding.

```sql
CREATE TABLE chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    content TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    start_char INTEGER,
    end_char INTEGER,
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id)
)
```

#### `vec_chunks` (Virtual Table)

Stores vector embeddings for similarity search.

```sql
CREATE VIRTUAL TABLE vec_chunks USING vec0(
    chunk_id TEXT PRIMARY KEY,
    embedding FLOAT[1536]     -- OpenAI text-embedding-3-small dimension
)
```

## Usage

### Initialize Database

```python
from auto_bedrock_chat_fastapi.vector_db import VectorDB

# Create/connect to database
db = VectorDB("knowledge_base.db")
```

### Add Documents

```python
db.add_document(
    doc_id="auth-guide-001",
    content="Complete authentication guide...",
    title="Authentication Guide",
    source="docs",
    source_url="https://docs.example.com/auth",
    topic="authentication",
    date_published="2026-01-08",
    metadata={"author": "Team", "version": "1.0"}
)
```

### Add Chunks with Embeddings

```python
# Assuming you have an embedding from OpenAI or similar
embedding = get_embedding("Use API keys for authentication")

db.add_chunk(
    chunk_id="auth-guide-001-chunk-0",
    document_id="auth-guide-001",
    content="Use API keys for authentication.",
    embedding=embedding,  # List of 1536 floats
    chunk_index=0,
    start_char=0,
    end_char=35
)
```

### Semantic Search

```python
# Get embedding for user query
query_embedding = get_embedding("how to authenticate")

# Search for similar chunks
results = db.semantic_search(
    query_embedding=query_embedding,
    limit=3,
    min_score=0.7,
    filters={
        "source": "docs",
        "date_after": "2025-01-01"
    }
)

# Results format
for result in results:
    print(f"Score: {result['similarity_score']}")
    print(f"Content: {result['content']}")
    print(f"Source: {result['source_url']}")
```

### Get Document

```python
doc = db.get_document("auth-guide-001")
print(doc['title'])
print(doc['content'])
```

### List Sources and Topics

```python
sources = db.list_sources()
# [{'source': 'docs', 'count': 25}, {'source': 'blog', 'count': 10}]

topics = db.list_topics()
# [{'topic': 'authentication', 'count': 15}, {'topic': 'api', 'count': 20}]
```

### Delete Document

```python
# Deletes document and all associated chunks/vectors
db.delete_document("auth-guide-001")
```

### Get Statistics

```python
stats = db.get_stats()
# {
#     'documents': 100,
#     'chunks': 500,
#     'vectors': 500,
#     'db_size_bytes': 52428800
# }
```

## Filtering Support

Semantic search supports the following filters:

- **`source`**: Filter by document source (e.g., "docs", "blog")
- **`topic`**: Filter by topic/category
- **`date_after`**: Only documents published after this date (ISO format)
- **`date_before`**: Only documents published before this date (ISO format)

Example:

```python
results = db.semantic_search(
    query_embedding=embedding,
    limit=5,
    min_score=0.7,
    filters={
        "source": "blog",
        "topic": "authentication",
        "date_after": "2025-01-01",
        "date_before": "2026-01-01"
    }
)
```

## Backup Strategy

### Manual Backup

```bash
# Simple file copy
cp knowledge_base.db knowledge_base_backup_$(date +%Y%m%d).db
```

### Automated Backup (Cron Job)

```bash
# Add to crontab for daily backups at 2 AM
0 2 * * * cp /path/to/knowledge_base.db /path/to/backups/kb_$(date +\%Y\%m\%d).db
```

### Using SQLite Backup API

```python
import sqlite3
import shutil
from datetime import datetime

def backup_database(source_db: str, backup_dir: str):
    """Backup database with timestamp."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{backup_dir}/kb_backup_{timestamp}.db"

    # Use shutil for simple copy
    shutil.copy2(source_db, backup_path)
    print(f"Backup created: {backup_path}")

# Usage
backup_database("knowledge_base.db", "./backups")
```

## Performance Considerations

### Strengths

- ✅ Zero latency for local file access
- ✅ No network overhead
- ✅ Simple deployment (single file)
- ✅ Good performance up to ~50k chunks

### Limitations

- ⚠️ Single-writer constraint
- ⚠️ Not ideal for distributed systems
- ⚠️ Performance degrades after ~100k vectors
- ⚠️ No built-in sharding/replication

### When to Migrate

Consider migrating to a production vector database when:

- Knowledge base exceeds 50k documents
- Need distributed/multi-region deployment
- Require high-concurrency writes
- Need advanced features (hybrid search, reranking)

### Migration Path

**To Pinecone:**

```python
import pinecone

# 1. Export from SQLite
cursor = db.conn.cursor()
cursor.execute("SELECT chunk_id, embedding FROM vec_chunks")

# 2. Import to Pinecone
pinecone.init(api_key="...")
index = pinecone.Index("knowledge-base")

vectors = []
for chunk_id, embedding_bytes in cursor:
    embedding = np.frombuffer(embedding_bytes, dtype=np.float32).tolist()
    vectors.append((chunk_id, embedding))

index.upsert(vectors=vectors)
```

**To pgvector:**

```python
import psycopg2

# Similar export process, insert into PostgreSQL with pgvector extension
```

## API Reference

### VectorDB Class

#### `__init__(db_path: str)`

Initialize database connection.

#### `add_document(...)`

Add a complete document with metadata.

#### `add_chunk(...)`

Add a chunk with its vector embedding.

#### `semantic_search(query_embedding, limit, min_score, filters)`

Search for similar chunks.

#### `get_document(doc_id)`

Retrieve document by ID.

#### `list_sources()`

Get all unique sources with counts.

#### `list_topics()`

Get all unique topics with counts.

#### `delete_document(doc_id)`

Delete document and all chunks.

#### `get_stats()`

Get database statistics.

#### `close()`

Close database connection.

## Troubleshooting

### Issue: "No module named 'sqlite_vec'"

```bash
poetry add sqlite-vec
```

### Issue: "No module named 'numpy'"

```bash
poetry add "numpy>=1.21,<2.0"
```

### Issue: Database locked

SQLite uses file-level locking. Ensure:

- Only one writer at a time
- Use `check_same_thread=False` for multi-threaded apps (already configured)

### Issue: Slow queries

- Add indexes on frequently filtered columns (already created)
- Consider chunk size optimization
- For >100k vectors, migrate to dedicated vector DB

## Next Steps

1. ✅ Vector DB setup complete
2. → Implement web crawler (Task 1.2)
3. → Create embedding pipeline (Task 1.3)
4. → Build semantic search endpoint (Task 1.4)
