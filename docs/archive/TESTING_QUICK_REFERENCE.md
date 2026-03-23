# Testing Quick Reference

## Pytest (Unit Tests) - Fast & Environmentless

```bash
# Run all unit tests
poetry run pytest tests/

# Run specific test file
poetry run pytest tests/test_vector_db.py -v

# Run with coverage
poetry run pytest tests/ --cov=auto_bedrock_chat_fastapi

# Watch mode (requires pytest-watch)
ptw tests/
```

**Characteristics**:

- ⚡ Fast (<5 seconds)
- 🔒 No setup required
- 🎭 All dependencies mocked
- ✅ 204 tests available

---

## Integration Tests - Real Services

```bash
# Run individual suite
python integration_testing/test_rag_semantic_search.py
python integration_testing/test_rag_quality.py
python integration_testing/test_rag_chat.py  # Requires running server

# Run all (skip WebSocket)
python integration_testing/run_all.py --skip-chat

# Run all (including WebSocket)
# Terminal 1:
uvicorn auto_bedrock_chat_fastapi.app:app --port 8001
# Terminal 2:
python integration_testing/run_all.py
```

**Characteristics**:

- 🐢 Slower (2-3 minutes)
- 🔧 Requires .env with AWS credentials
- 🌐 Uses real Bedrock API
- 📊 23 integration tests available

---

## Prerequisites

### Unit Tests (pytest)

```bash
# Nothing! Just run pytest
poetry run pytest tests/
```

### Integration Tests

```bash
# 1. AWS credentials in .env
cat > .env << EOF
AWS_ACCESS_KEY_ID=your-key
AWS_SECRET_ACCESS_KEY=your-secret
AWS_REGION=us-east-1
EOF

# 2. Populated database
python -m auto_bedrock_chat_fastapi.commands.kb populate --config kb_sources.yaml

# 3. (Optional) Running server for WebSocket tests
uvicorn auto_bedrock_chat_fastapi.app:app --port 8001
```

---

## When to Run What

| Scenario               | Command                                              |
| ---------------------- | ---------------------------------------------------- |
| **Every change**       | `poetry run pytest tests/`                           |
| **Before commit**      | `python integration_testing/run_all.py --skip-chat`  |
| **Before deploy**      | `python integration_testing/run_all.py` (full)       |
| **Debug failing test** | `poetry run pytest tests/test_file.py::test_name -v` |
| **Check coverage**     | `poetry run pytest tests/ --cov`                     |

---

## Troubleshooting

### Pytest Issues

```bash
# Clear cache
poetry run pytest --cache-clear

# Verbose output
poetry run pytest -vv -s

# Stop at first failure
poetry run pytest -x
```

### Integration Test Issues

```bash
# Check database exists
ls -lh examples/fastAPI/fastapi_kb.db

# Check .env loaded
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print(os.getenv('AWS_ACCESS_KEY_ID'))"

# Test database connection
poetry run python -c "
from auto_bedrock_chat_fastapi.vector_db import VectorDB
db = VectorDB('examples/fastAPI/fastapi_kb.db')
print('✅ Connected')
db.close()
"
```

---

## File Locations

```
auto-bedrock-chat-fastapi/
├── tests/                           # Pytest unit tests (204 tests)
│   ├── conftest.py                  # Clean fixtures, no .env
│   ├── test_authentication.py
│   ├── test_vector_db.py
│   └── ... (all environmentless)
│
├── integration_testing/             # Integration tests (23 tests)
│   ├── README.md                    # Full documentation
│   ├── run_all.py                   # Test runner
│   ├── test_rag_semantic_search.py  # 6 tests
│   ├── test_rag_quality.py          # 17 tests
│   └── test_rag_chat.py             # WebSocket tests
│
└── docs/
    ├── TEST_ARCHITECTURE_REFACTORING.md  # Detailed explanation
    └── TESTING_QUICK_REFERENCE.md        # This file
```

---

## Example Output

### Pytest Success

```
$ poetry run pytest tests/ -v
======================== 204 passed in 4.23s ========================
```

### Integration Test Success

```
$ python integration_testing/test_rag_semantic_search.py

🧪 Running Integration Tests: RAG Semantic Search
============================================================

✅ PASS: Basic semantic search query
✅ PASS: Semantic search with source filter
✅ PASS: Low threshold search (0.5)
✅ PASS: Irrelevant query (pasta cooking)
✅ PASS: Empty results for nonsense query
✅ PASS: Result structure validation

============================================================
Summary:
------------------------------------------------------------
Total: 6 tests
Passed: 6 ✅
Failed: 0 ❌
Success Rate: 100.0%
```

---

## Quick Commands (Copy-Paste)

```bash
# Unit tests only
poetry run pytest tests/ -v

# Integration tests (no WebSocket)
python integration_testing/run_all.py --skip-chat

# Full test suite
poetry run pytest tests/ && python integration_testing/run_all.py --skip-chat

# Individual integration test
python integration_testing/test_rag_semantic_search.py

# Coverage report
poetry run pytest tests/ --cov=auto_bedrock_chat_fastapi --cov-report=html
```

---

**Last Updated**: January 15, 2026
