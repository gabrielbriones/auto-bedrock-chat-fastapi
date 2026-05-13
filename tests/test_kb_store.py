"""Tests for KB store abstraction layer: BaseKBStore and factory."""

import os
import tempfile

import numpy as np
import pytest

from auto_bedrock_chat_fastapi.db import create_kb_store
from auto_bedrock_chat_fastapi.db.kb_base import BaseKBStore
from auto_bedrock_chat_fastapi.db.kb_sqlite import SQLiteKBStore

# ---------------------------------------------------------------------------
# SQLiteKBStore is a concrete BaseKBStore
# ---------------------------------------------------------------------------


class TestSQLiteKBStoreIsBaseKBStore:
    """Ensure the refactored class satisfies the interface."""

    def test_isinstance(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            store = SQLiteKBStore(db_path)
            assert isinstance(store, BaseKBStore)
            store.close()
        finally:
            os.unlink(db_path)

    def test_has_all_abstract_methods(self):
        """Verify every abstract method declared on BaseKBStore exists on SQLiteKBStore."""
        for name in (
            "add_document",
            "add_chunk",
            "semantic_search",
            "keyword_search",
            "hybrid_search",
            "get_document",
            "delete_document",
            "list_sources",
            "list_topics",
            "get_stats",
            "close",
        ):
            assert hasattr(SQLiteKBStore, name), f"Missing method: {name}"


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


class TestCreateKBStore:
    """Tests for the create_kb_store factory."""

    def _make_config(self, **overrides):
        """Build a minimal ChatConfig for testing."""
        from auto_bedrock_chat_fastapi.config import ChatConfig

        defaults = {
            "BEDROCK_MODEL_ID": "anthropic.claude-sonnet-4-5-20250929-v1:0",
            "AWS_REGION": "us-east-1",
        }
        defaults.update(overrides)

        # Use env-var style init (alias names)
        return ChatConfig(**defaults)

    def test_sqlite_default(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            config = self._make_config(KB_DATABASE_PATH=db_path)
            store = create_kb_store(config)
            assert isinstance(store, SQLiteKBStore)
            assert isinstance(store, BaseKBStore)
            store.close()
        finally:
            os.unlink(db_path)

    def test_sqlite_explicit(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            config = self._make_config(BEDROCK_KB_STORAGE_TYPE="sqlite", KB_DATABASE_PATH=db_path)
            store = create_kb_store(config)
            assert isinstance(store, SQLiteKBStore)
            store.close()
        finally:
            os.unlink(db_path)

    def test_unknown_type_raises(self):
        config = self._make_config(BEDROCK_KB_STORAGE_TYPE="redis")
        with pytest.raises(ValueError, match="Unknown kb_storage_type"):
            create_kb_store(config)


# ---------------------------------------------------------------------------
# Fixtures shared with existing tests
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_embedding():
    """Generate a sample embedding vector."""
    return np.random.rand(1536).tolist()
