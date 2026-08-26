"""Tests for kb_populate() aggregating and reporting per-item ingestion errors.

Covers the CLI regression flagged in PR review round 3: previously
``result["errors"]`` from ingest_web_source()/ingest_local_source() was
discarded entirely, so a run where every page/file failed to index still
logged "population complete" and returned True.
"""

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from autolangchat.commands.kb import kb_populate


def _real_import(name):
    """Import ``name`` fresh, bypassing a stale stub.

    tests/_autolangchat_imports.py's load_module() temporarily swaps
    ``sys.modules`` entries (e.g. ``autolangchat.db``) for bare stub
    ``types.ModuleType`` objects while file-loading another test's target
    module, then restores the previous entry. If a stub is still cached
    here when this runs, a plain ``import`` would silently return it
    (missing all real attributes like ``create_kb_store``) instead of the
    real, fully-executed module -- so drop it and re-import for real.
    """
    module = sys.modules.get(name)
    if module is not None and getattr(module, "__file__", None) is None:
        del sys.modules[name]
        module = None
    return module or importlib.import_module(name)


def _make_config(tmp_path, sources):
    config_path = tmp_path / "kb_sources.yaml"
    config_path.write_text(yaml.safe_dump({"knowledge_base": {"enabled": True, "sources": sources}}))
    db_path = tmp_path / "kb.db"
    config = SimpleNamespace(
        enable_rag=True,
        kb_sources_config=str(config_path),
        kb_database_path=str(db_path),
        kb_storage_type="sqlite",
        kb_embedding_model="fake-model",
        kb_chunk_size=512,
        kb_chunk_overlap=100,
    )
    return config, str(config_path), str(db_path)


@pytest.mark.asyncio
async def test_kb_populate_returns_false_and_logs_errors_from_web_source(tmp_path, caplog):
    sources = [{"name": "web-src", "type": "web", "urls": ["https://example.com"]}]
    config, config_path, db_path = _make_config(tmp_path, sources)

    web_result = {
        "documents": 1,
        "chunks": 3,
        "errors": ["failed to index https://example.com/broken: boom"],
    }
    bedrock_embeddings_mod = _real_import("autolangchat.rag.bedrock_embeddings")
    db_mod = _real_import("autolangchat.db")
    kb_ingestion_mod = _real_import("autolangchat.rag.kb_ingestion")

    with (
        patch.object(bedrock_embeddings_mod, "BedrockEmbeddingClient", return_value=MagicMock()),
        patch.object(db_mod, "create_kb_store", return_value=MagicMock()),
        patch.object(kb_ingestion_mod, "ingest_web_source", AsyncMock(return_value=web_result)),
        caplog.at_level("WARNING"),
    ):
        result = await kb_populate(config_path=config_path, db_path=db_path, config=config)

    assert result is False
    assert any("boom" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_kb_populate_returns_true_when_no_errors(tmp_path):
    sources = [{"name": "web-src", "type": "web", "urls": ["https://example.com"]}]
    config, config_path, db_path = _make_config(tmp_path, sources)

    web_result = {"documents": 1, "chunks": 3, "errors": []}
    bedrock_embeddings_mod = _real_import("autolangchat.rag.bedrock_embeddings")
    db_mod = _real_import("autolangchat.db")
    kb_ingestion_mod = _real_import("autolangchat.rag.kb_ingestion")

    with (
        patch.object(bedrock_embeddings_mod, "BedrockEmbeddingClient", return_value=MagicMock()),
        patch.object(db_mod, "create_kb_store", return_value=MagicMock()),
        patch.object(kb_ingestion_mod, "ingest_web_source", AsyncMock(return_value=web_result)),
    ):
        result = await kb_populate(config_path=config_path, db_path=db_path, config=config)

    assert result is True


@pytest.mark.asyncio
async def test_kb_populate_aggregates_errors_from_web_and_local_sources(tmp_path):
    sources = [
        {"name": "web-src", "type": "web", "urls": ["https://example.com"]},
        {"name": "local-src", "type": "local", "path": str(tmp_path)},
    ]
    config, config_path, db_path = _make_config(tmp_path, sources)

    web_result = {"documents": 1, "chunks": 2, "errors": ["web error"]}
    local_result = {"documents": 0, "chunks": 0, "errors": ["local error"]}
    bedrock_embeddings_mod = _real_import("autolangchat.rag.bedrock_embeddings")
    db_mod = _real_import("autolangchat.db")
    kb_ingestion_mod = _real_import("autolangchat.rag.kb_ingestion")

    with (
        patch.object(bedrock_embeddings_mod, "BedrockEmbeddingClient", return_value=MagicMock()),
        patch.object(db_mod, "create_kb_store", return_value=MagicMock()),
        patch.object(kb_ingestion_mod, "ingest_web_source", AsyncMock(return_value=web_result)),
        patch.object(kb_ingestion_mod, "ingest_local_source", AsyncMock(return_value=local_result)),
    ):
        result = await kb_populate(config_path=config_path, db_path=db_path, config=config)

    assert result is False
