# FastAPI Assistant with RAG

This example demonstrates a FastAPI assistant powered by RAG (Retrieval-Augmented Generation) that uses the official FastAPI documentation as its knowledge base.

## Features

- 🤖 AI assistant specialized in FastAPI
- 📚 Knowledge base built from official FastAPI docs (tutorial, advanced guide, reference)
- 🔍 Retrieves relevant documentation chunks to enhance responses
- 💬 Real-time chat interface
- 🎯 Accurate, up-to-date answers based on official documentation

## Quick Start

### 1. Install Dependencies

```bash
cd /home/gbriones/auto-bedrock-chat-fastapi
poetry install
```

### 2. Configure AWS Credentials

Ensure your AWS credentials are configured (for Bedrock access):

```bash
export AWS_REGION=us-east-1
export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret
```

Or use AWS CLI profile / IAM role.

### 3. Run the Example

```bash
cd examples/fastAPI
poetry run python app_rag.py
```

**First Run**: The app will automatically crawl the FastAPI documentation and build the knowledge base (2-3 minutes).

**Subsequent Runs**: The knowledge base is cached in `fastapi_kb.db` and loads instantly.

### 4. Access the Chat Interface

Open your browser to:

- **Chat UI**: http://localhost:8001/bedrock-chat/ui
- **API Docs**: http://localhost:8001/docs

## Try These Example Queries

Once the app is running, try asking:

- "How do I create a FastAPI application?"
- "Show me an example of path parameters"
- "How do I handle file uploads in FastAPI?"
- "What's the difference between Query and Path parameters?"
- "How do I add authentication to my FastAPI app?"
- "Show me how to use dependency injection"
- "How do I handle CORS in FastAPI?"

## How It Works

1. **Knowledge Base Population**: On first run, the app crawls:

   - FastAPI Tutorial (~100 pages)
   - Advanced User Guide (~50 pages)
   - API Reference (~30 pages)

2. **Document Processing**:

   - Text is chunked into 512-token segments with 100-token overlap
   - Each chunk is embedded using AWS Bedrock Titan Embed v1 (1536 dimensions)
   - Embeddings are stored in SQLite vector database

3. **RAG Query Flow**:
   - User asks a question
   - Question is embedded
   - Top 5 relevant chunks are retrieved (similarity > 0.7)
   - Chunks are injected into the system prompt
   - AI generates response using documentation context

## Configuration

### RAG Settings

Edit `app_rag.py` to customize:

```python
bedrock_chat = add_bedrock_chat(
    app,
    enable_rag=True,
    kb_top_k_results=5,           # Number of chunks to retrieve
    kb_similarity_threshold=0.7,   # Minimum relevance score
    kb_populate_on_startup=True,   # Auto-populate on first run
    # ... other settings
)
```

### Knowledge Base Sources

Edit `kb_sources_fastapi.yaml` to add/remove documentation sources:

```yaml
knowledge_base:
  enabled: true
  sources:
    - name: "FastAPI Tutorial"
      type: "web"
      urls:
        - "https://fastapi.tiangolo.com/tutorial/"
      max_pages: 100
```

## Manual KB Management

### KB Command Reference

The Knowledge Base can be managed using CLI commands. The command format differs depending on whether you're developing this repository or using the package in your own project:

#### When Developing This Repository

Use `poetry run` to execute commands within the repository:

```bash
# Check KB status
ENABLE_RAG=true poetry run python -m auto_bedrock_chat_fastapi.commands.kb status \
  --config examples/fastAPI/kb_sources_fastapi.yaml \
  --db examples/fastAPI/fastapi_kb.db

# Rebuild KB (force refresh)
ENABLE_RAG=true poetry run python -m auto_bedrock_chat_fastapi.commands.kb populate \
  --config examples/fastAPI/kb_sources_fastapi.yaml \
  --db examples/fastAPI/fastapi_kb.db \
  --force

# Clear KB
ENABLE_RAG=true poetry run python -m auto_bedrock_chat_fastapi.commands.kb clear \
  --db examples/fastAPI/fastapi_kb.db \
  --yes
```

#### When Using as an Installed Package

If you've installed `auto-bedrock-chat-fastapi` in your own project (via pip or poetry), use these commands:

```bash
# Check KB status
ENABLE_RAG=true python -m auto_bedrock_chat_fastapi.commands.kb status \
  --config path/to/your/kb_sources.yaml \
  --db path/to/your/kb.db

# Populate/rebuild KB
ENABLE_RAG=true python -m auto_bedrock_chat_fastapi.commands.kb populate \
  --config path/to/your/kb_sources.yaml \
  --db path/to/your/kb.db \
  --force

# Clear KB
ENABLE_RAG=true python -m auto_bedrock_chat_fastapi.commands.kb clear \
  --db path/to/your/kb.db \
  --yes
```

**Key Differences:**

- **Development**: Use `poetry run python -m` (runs within Poetry's virtualenv)
- **Production/Installed**: Use `python -m` or your project's virtualenv activation
- **Paths**: Adjust `--config` and `--db` paths to match your project structure

#### Available Commands

| Command    | Description                   | Common Options                |
| ---------- | ----------------------------- | ----------------------------- |
| `status`   | Show KB statistics and health | `--config`, `--db`            |
| `populate` | Build/update knowledge base   | `--config`, `--db`, `--force` |
| `clear`    | Delete all KB data            | `--db`, `--yes`               |
| `search`   | Test semantic search          | `--db`, `--query`, `--limit`  |

#### Example: Production Setup

If you've installed the package in your project:

```bash
# Install the package
pip install git+https://github.com/gabrielbriones/auto-bedrock-chat-fastapi.git

# Create your KB sources config
cat > my_kb_sources.yaml << EOF
---
knowledge_base:
  enabled: true
  sources:
    - name: "My Documentation"
      type: "web"
      urls:
        - "https://docs.myproject.com/"
      max_pages: 50
EOF

# Populate your knowledge base
ENABLE_RAG=true python -m auto_bedrock_chat_fastapi.commands.kb populate \
  --config my_kb_sources.yaml \
  --db my_project_kb.db

# Check status
ENABLE_RAG=true python -m auto_bedrock_chat_fastapi.commands.kb status \
  --config my_kb_sources.yaml \
  --db my_project_kb.db
```

## Troubleshooting

### Issue: "KB population failed"

**Solution**: Check your internet connection and ensure fastapi.tiangolo.com is accessible.

### Issue: "AWS credentials not found"

**Solution**: Configure AWS credentials:

```bash
aws configure
# OR
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
```

### Issue: "KB is empty but kb_allow_empty=True"

**Expected behavior**: The app starts but RAG is disabled. Check logs for crawl errors.

### Issue: Slow first run

**Expected behavior**: Crawling 180+ pages and generating embeddings takes 2-3 minutes. Subsequent runs are instant.

## Architecture

```
User Query
    ↓
[Embed Query]
    ↓
[Vector DB Search] → Top 5 chunks (similarity > 0.7)
    ↓
[Inject into System Prompt]
    ↓
[Claude 4.5 Sonnet] → Generate response
    ↓
User Response (with citations)
```

## Files

- `app_rag.py` - Main application with RAG configuration
- `kb_sources_fastapi.yaml` - Knowledge base sources definition
- `fastapi_kb.db` - SQLite vector database (auto-generated)
- `README.md` - This file

## Performance

- **Initial crawl**: 2-3 minutes
- **Subsequent startups**: <2 seconds
- **Query latency**: 1-3 seconds (including RAG retrieval)
- **KB size**: ~5-10 MB (180+ pages, ~1000 chunks)
- **Cost per query**: ~$0.005 (Bedrock Claude + embeddings)

## Next Steps

1. **Customize sources**: Edit `kb_sources_fastapi.yaml` to add your own docs
2. **Tune retrieval**: Adjust `kb_top_k_results` and `kb_similarity_threshold`
3. **Custom prompt**: Modify `system_prompt` for different behavior
4. **Add feedback**: Implement thumbs up/down to improve retrieval

## Related Examples

- `../embedding_examples.py` - Embedding pipeline examples
- `../crawler_examples.py` - Web crawler examples

## Support

For issues or questions:

- Check logs for detailed error messages
- See main project documentation
- Review Task 1.4 in HYBRID_KB_IMPLEMENTATION_TRACKER.md
